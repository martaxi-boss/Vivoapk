using System.Text.Json;

namespace Jellyfin.Plugin.VivoapkMegaAuth;

/// <summary>
/// A short-lived, item-scoped media destination returned by the trusted private
/// backend. This is a proposed internal contract, not a documented Mega API.
/// It must not be constructed from the account username/password.
/// </summary>
public sealed class AuthorizedPlaybackGrant
{
    private readonly string _url;
    private readonly string _assetId;
    private readonly string _kind;
    private readonly DateTimeOffset _expiresAt;

    private AuthorizedPlaybackGrant(string url, string assetId, string kind, DateTimeOffset expiresAt)
    {
        _url = url;
        _assetId = assetId;
        _kind = kind;
        _expiresAt = expiresAt;
    }

    public override string ToString() => "AuthorizedPlaybackGrant(redacted)";

    public static AuthorizedPlaybackGrant? Read(JsonElement row)
    {
        if (row.ValueKind != JsonValueKind.Object ||
            !row.TryGetProperty("playback_grant", out var grant) ||
            grant.ValueKind != JsonValueKind.Object ||
            !ReadString(grant, "url", out var url) ||
            !ReadString(grant, "asset_id", out var assetId) ||
            !ReadString(grant, "kind", out var kind) ||
            !grant.TryGetProperty("expires_at", out var expiry) ||
            expiry.ValueKind != JsonValueKind.String ||
            !expiry.TryGetDateTimeOffset(out var expiresAt))
            return null;
        return new AuthorizedPlaybackGrant(url, assetId, kind, expiresAt);
    }

    private static bool ReadString(JsonElement obj, string key, out string value)
    {
        value = string.Empty;
        if (!obj.TryGetProperty(key, out var element) || element.ValueKind != JsonValueKind.String)
            return false;
        value = element.GetString() ?? string.Empty;
        return value.Length > 0;
    }

    public string? GetValidatedUrl(string expectedAssetId, string expectedKind,
        string approvedOrigin, string username, string password, DateTimeOffset now)
    {
        // Short expiry is a conservative client-side acceptance limit; the
        // origin must independently enforce expiry, entitlement and revocation.
        if (_assetId != expectedAssetId || _kind != expectedKind ||
            expectedKind is not ("live" or "vod" or "series") ||
            _expiresAt <= now || _expiresAt > now.AddMinutes(2) ||
            string.IsNullOrWhiteSpace(password) || string.IsNullOrWhiteSpace(username) ||
            _url.Length > 4096 || _url.Any(c => c <= 32 || c >= 127) ||
            _url.Contains('\\') ||
            !Uri.IsWellFormedUriString(_url, UriKind.Absolute) ||
            !Uri.TryCreate(_url, UriKind.Absolute, out var target) ||
            !Uri.TryCreate(approvedOrigin, UriKind.Absolute, out var origin) ||
            target.Scheme != Uri.UriSchemeHttps || origin.Scheme != Uri.UriSchemeHttps ||
            target.IsLoopback || origin.IsLoopback ||
            target.UserInfo.Length != 0 || target.Fragment.Length != 0 ||
            origin.UserInfo.Length != 0 || origin.Fragment.Length != 0 ||
            origin.Query.Length != 0 || origin.AbsolutePath != "/" ||
            target.AbsolutePath == "/" ||
            !string.Equals(target.IdnHost, origin.IdnHost, StringComparison.OrdinalIgnoreCase) ||
            target.Port != origin.Port)
            return null;

        var decoded = Uri.UnescapeDataString(target.AbsoluteUri);
        if (decoded.Any(char.IsControl) ||
            _url.Contains(password, StringComparison.Ordinal) ||
            decoded.Contains(password, StringComparison.Ordinal) ||
            decoded.Split('/', '?', '&', '=', ';').Contains(username, StringComparer.Ordinal))
            return null;
        return target.AbsoluteUri;
    }
}
