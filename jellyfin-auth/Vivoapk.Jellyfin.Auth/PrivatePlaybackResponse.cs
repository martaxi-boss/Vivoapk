using System.Text.Json.Nodes;

namespace Jellyfin.Plugin.VivoapkMegaAuth;

/// <summary>Common private PlaybackInfo response sanitation. Reject unexpected
/// capability locations; only native source Path and EncoderPath may be removed.
/// Not a substitute for authorizing every endpoint that returns media metadata.</summary>
internal static class PrivatePlaybackResponse
{
    internal static bool ContainsInternalPath(string text) =>
        text.Contains("/vivoapk/", StringComparison.OrdinalIgnoreCase)
        || text.Contains("%2fvivoapk%2f", StringComparison.OrdinalIgnoreCase)
        || text.Contains("private-hls-", StringComparison.OrdinalIgnoreCase)
        || text.Contains("/srv/projects/Vivoapk/run/", StringComparison.OrdinalIgnoreCase);

    internal static void SanitizePlaybackInfo(JsonNode node)
    {
        if (node is JsonObject obj)
        {
            foreach (var key in obj.Select(pair => pair.Key).ToArray())
            {
                var child = obj[key];
                if (child is JsonValue value && value.TryGetValue<string>(out var text)
                    && ContainsInternalPath(text))
                {
                    if (!key.Equals("Path", StringComparison.OrdinalIgnoreCase)
                        && !key.Equals("EncoderPath", StringComparison.OrdinalIgnoreCase))
                        throw new InvalidOperationException("Unexpected internal media reference");
                    obj.Remove(key);
                }
                else if (child is not null)
                    SanitizePlaybackInfo(child);
            }
        }
        else if (node is JsonArray array)
        {
            foreach (var child in array)
                if (child is not null)
                    SanitizePlaybackInfo(child);
        }
        else if (node is JsonValue value && value.TryGetValue<string>(out var text)
            && ContainsInternalPath(text))
            throw new InvalidOperationException("Unexpected internal media reference");
    }


    // Both guarded native routes serve MPEG-TS HLS segments: rolling live
    // and finite hls1/main. Advertise their supported segment container
    // before the client selects fragmented MP4. Preserve the finite source
    // metadata and native seek parameters; only the HLS segment format changes.
    internal static void ConstrainHlsSegments(JsonNode node, Guid item)
    {
        if (node is not JsonObject root || root["MediaSources"] is not JsonArray sources
            || sources.Count != 1 || sources[0] is not JsonObject media)
            throw new InvalidOperationException("Invalid private media negotiation");
        if (media["TranscodingUrl"] is not JsonValue value
            || !value.TryGetValue<string>(out var route) || string.IsNullOrEmpty(route)
            || route.StartsWith("//", StringComparison.Ordinal) || route.Contains("://", StringComparison.Ordinal)
            || route.Contains('\\') || route.Contains('#'))
            throw new InvalidOperationException("Invalid private live route");
        var parts = route.Split('?', 2);
        var path = parts[0].TrimStart('/').Split('/');
        if (item == Guid.Empty || path.Length != 3
            || !path[0].Equals("Videos", StringComparison.OrdinalIgnoreCase)
            || !Guid.TryParse(path[1], out var actual) || actual != item
            || !(path[2].Equals("master.m3u8", StringComparison.OrdinalIgnoreCase)
                || path[2].Equals("main.m3u8", StringComparison.OrdinalIgnoreCase)
                || path[2].Equals("live.m3u8", StringComparison.OrdinalIgnoreCase)))
            throw new InvalidOperationException("Unbound private live route");
        var query = Microsoft.AspNetCore.WebUtilities.QueryHelpers.ParseQuery(
            parts.Length == 2 ? "?" + parts[1] : "");
        if (query.Any(pair => pair.Value.Count != 1))
            throw new InvalidOperationException("Ambiguous private live query");
        query["SegmentContainer"] = "ts";
        media["TranscodingUrl"] = Microsoft.AspNetCore.WebUtilities.QueryHelpers.AddQueryString(
            parts[0], query.ToDictionary(pair => pair.Key, pair => (string?)pair.Value[0]));
    }

}
