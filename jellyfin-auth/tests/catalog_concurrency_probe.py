"""Disposable reproduction probe for upstream Jellyfin issue #18229.

Uses the existing synthetic server, two mock accounts and six channels each.
No real provider, production server, cache deletion or authorization changes.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
from urllib.parse import parse_qs, urlsplit

import jellyfin_12_4_smoke as smoke

if not smoke.CATEGORY_MODE or smoke.SERVER_PLAYBACK:
    raise RuntimeError("Probe requires synthetic category mode only")

original_request = smoke.request
targets = {}
observations = []
settled_ids = []

def summarize(result, expected_names):
    status, response = result
    items = response.get("Items", []) if isinstance(response, dict) else []
    ids = {item.get("Id") for item in items}
    names = {item.get("Name") for item in items}
    complete = (status == 200 and len(items) == 6 and len(ids) == 6
                and None not in ids and names == expected_names)
    return {"status": status, "count": len(items), "complete": complete}, ids

def checked_request(method, uri, data=None, token=None, client_name=None):
    parsed = urlsplit(uri)
    is_listing = (method == "GET" and parsed.path.startswith("/Channels/")
                  and parsed.path.endswith("/Items"))
    folder_id = parse_qs(parsed.query).get("FolderId", [None])[0]
    if is_listing and folder_id in targets:
        label = targets.pop(folder_id)
        expected = {"Canal CI " + label + ("" if i == 0 else " " + str(i))
                    for i in range(6)}
        barrier = threading.Barrier(12)

        def cold_read():
            barrier.wait(timeout=5)
            return original_request(method, uri, data, token, client_name)

        with ThreadPoolExecutor(max_workers=12) as workers:
            futures = [workers.submit(cold_read) for _ in range(12)]
            results = [summarize(f.result(), expected) for f in futures]
        # Only the first concurrent responses count for the gate.
        # A later response distinguishes an indexing race from persistent loss.
        settled = original_request(method, uri, data, token, client_name)
        settled_result, ids = summarize(settled, expected)
        settled_ids.append(ids)
        observation = {
            "fixture": label,
            "cold_reads": [item[0] for item in results],
            "settled": settled_result,
            "stable_ids": all(item[1] == ids for item in results),
        }
        observations.append(observation)
        print("JELLYFIN_CATALOG_CONCURRENT_COLD_READ=" +
              json.dumps(observation, sort_keys=True), flush=True)
        # Preserve the regular smoke assertions; the independent gate below
        # fails even if the subsequent read repaired the visible catalogue.
        return settled

    status, response = original_request(method, uri, data, token, client_name)
    if is_listing and status == 200 and isinstance(response, dict):
        for item in response.get("Items", []):
            name = item.get("Name")
            if name in ("Portugal Azul", "Portugal Verde") and item.get("Id"):
                label = name.split()[-1]
                if not any(x["fixture"] == label for x in observations):
                    targets[item["Id"]] = label
    return status, response

smoke.request = checked_request
smoke.main()
if (len(observations) != 2 or len(settled_ids) != 2
        or settled_ids[0] & settled_ids[1]
        or any(not x["settled"]["complete"] or not x["stable_ids"]
               or not all(r["complete"] for r in x["cold_reads"])
               for x in observations)):
    raise RuntimeError("Cold catalogue concurrency failed or was not fully exercised")
print("JELLYFIN_CATALOG_CONCURRENT_COLD_READS=PASS", flush=True)
