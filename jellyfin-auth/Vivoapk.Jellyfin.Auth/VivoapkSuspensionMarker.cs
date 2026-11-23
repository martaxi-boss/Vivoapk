using MediaBrowser.Common.Configuration;

namespace Jellyfin.Plugin.VivoapkMegaAuth;

/// <summary>
/// A non-secret, per-GUID ownership marker. Only a block created by the VivoAPK
/// entitlement task may be automatically undone following a provider renewal.
/// Never infer ownership from an arbitrary Jellyfin disabled flag.
/// </summary>
internal sealed class VivoapkSuspensionMarker
{
    private const string Value = "VIVOAPK_ENTITLEMENT_SUSPENSION_V1";
    private readonly string _root;

    public VivoapkSuspensionMarker(IApplicationPaths paths)
    {
        _root = Path.Combine(paths.DataPath, "vivoapk-entitlement-suspensions");
    }

    private string GetPath(Guid id)
    {
        if (id == Guid.Empty)
            throw new ArgumentException("Invalid Jellyfin GUID.", nameof(id));
        return Path.Combine(_root, id.ToString("N") + ".marker");
    }

    private void CheckRoot()
    {
        if (Directory.Exists(_root) &&
            (File.GetAttributes(_root) & FileAttributes.ReparsePoint) != 0)
            throw new InvalidOperationException("Refuse symbolic entitlement marker directory.");
    }

    public bool IsOwned(Guid id)
    {
        CheckRoot();
        var path = GetPath(id);
        if (!File.Exists(path))
            return false;
        if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
            throw new InvalidOperationException("Refuse linked entitlement marker.");
        return string.Equals(File.ReadAllText(path).Trim(), Value, StringComparison.Ordinal);
    }

    public void Mark(Guid id)
    {
        CheckRoot();
        Directory.CreateDirectory(_root);
        CheckRoot();
        var path = GetPath(id);
        if (IsOwned(id))
            return;
        if (File.Exists(path))
            throw new InvalidOperationException("Refuse unexpected entitlement marker.");
        using (var file = new FileStream(path, FileMode.CreateNew, FileAccess.Write,
                   FileShare.None, 256, FileOptions.WriteThrough))
        using (var writer = new StreamWriter(file))
        {
            writer.WriteLine(Value);
        }
        if (OperatingSystem.IsLinux())
            File.SetUnixFileMode(path, UnixFileMode.UserRead | UnixFileMode.UserWrite);
    }

    public void Clear(Guid id)
    {
        if (IsOwned(id))
            File.Delete(GetPath(id));
    }
}
