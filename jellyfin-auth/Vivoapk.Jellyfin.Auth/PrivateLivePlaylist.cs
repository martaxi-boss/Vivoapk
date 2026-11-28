using System.Globalization;
using System.Text;

namespace Jellyfin.Plugin.VivoapkMegaAuth;

/// <summary>Carry the authenticated Jellyfin identity onto only the exact
/// native live job's relative TS references. Never decorate an origin URL.</summary>
internal static class PrivateLivePlaylist
{
    private static readonly UTF8Encoding Utf8 = new(false, true);

    internal static bool TryAuthorize(ReadOnlySpan<byte> playlist, string nativePath,
        string token, string device, string session, Guid item, out byte[] content)
    {
        content = [];
        if (playlist.Length is 0 or > 262144 || item == Guid.Empty
            || token.Length is 0 or > 512
            || token.Any(c => char.IsWhiteSpace(c) || char.IsControl(c) || c is ',' or '"')
            || device.Length is 0 or > 1024 || device.Any(char.IsControl)
            || !Guid.TryParse(session, out _))
            return false;
        var stem = Path.GetFileNameWithoutExtension(nativePath);
        if (stem.Length is 0 or > 128 || !stem.All(char.IsAsciiHexDigit))
            return false;
        string text;
        try { text = Utf8.GetString(playlist); }
        catch (DecoderFallbackException) { return false; }
        if (!text.StartsWith("#EXTM3U\n", StringComparison.Ordinal)
            && !text.StartsWith("#EXTM3U\r\n", StringComparison.Ordinal))
            return false;
        var prefix = "hls/" + stem + "/" + stem;
        var query = "?ApiKey=" + Uri.EscapeDataString(token)
            + "&DeviceId=" + Uri.EscapeDataString(device)
            + "&PlaySessionId=" + Uri.EscapeDataString(session)
            + "&MediaSourceId=" + item.ToString("N", CultureInfo.InvariantCulture);
        var lines = text.Split('\n');
        for (var i = 0; i < lines.Length; i++)
        {
            var carriage = lines[i].EndsWith('\r');
            var line = carriage ? lines[i][..^1] : lines[i];
            if (line.Length == 0 || line[0] == '#')
                continue;
            if (!line.StartsWith(prefix, StringComparison.Ordinal)
                || !line.EndsWith(".ts", StringComparison.Ordinal))
                return false;
            var count = line.Length - prefix.Length - 3;
            if (count is <= 0 or > 10
                || !line.AsSpan(prefix.Length, count).ToArray().All(char.IsAsciiDigit))
                return false;
            lines[i] = line + query + (carriage ? "\r" : "");
        }
        content = Utf8.GetBytes(string.Join('\n', lines));
        return true;
    }
}
