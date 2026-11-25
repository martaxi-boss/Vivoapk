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

}
