using System.Collections.Concurrent;
using Jellyfin.Data;
using Jellyfin.Database.Implementations.Entities;
using Jellyfin.Database.Implementations.Enums;
using MediaBrowser.Common.Configuration;
using MediaBrowser.Controller.Library;
using MediaBrowser.Model.Configuration;
using MediaBrowser.Model.Entities;

namespace Jellyfin.Plugin.VivoapkMegaAuth;

/// <summary>
/// Creates a separate, initially EMPTY native movie library for a successfully
/// authenticated Mega subscriber, without provisioning any provider credentials,
/// remote URLs or media files. Opt-in until isolation tests are accepted.
/// </summary>
internal static class VivoapkPrivateLibraryProvisioner
{
    private static readonly ConcurrentDictionary<Guid, SemaphoreSlim> UserLocks = new();

    public static async Task EnsureAsync(
        User user,
        IUserManager users,
        ILibraryManager libraries,
        IApplicationPaths appPaths)
    {
        var pluginProviderId = typeof(MegaAuthenticationProvider).FullName;
        if (user.Id == Guid.Empty ||
            !string.Equals(user.AuthenticationProviderId, pluginProviderId, StringComparison.Ordinal))
        {
            throw new InvalidOperationException("Cannot provision a non-VivoAPK identity.");
        }

        var gate = UserLocks.GetOrAdd(user.Id, _ => new SemaphoreSlim(1, 1));
        await gate.WaitAsync().ConfigureAwait(false);
        try
        {
            // The ID is a stable opaque identifier; never place usernames in folder names.
            var opaqueId = user.Id.ToString("N");
            var libraryName = "VivoAPK-" + opaqueId;
            var root = Path.Combine(appPaths.DataPath, "vivoapk-private-libraries");
            var sourceFolder = Path.Combine(root, opaqueId);

            // Refuse symlinks/reparse points along the plugin-managed directory.
            AssertNotLink(root);
            AssertNotLink(sourceFolder);
            Directory.CreateDirectory(sourceFolder);
            AssertNotLink(root);
            AssertNotLink(sourceFolder);

            var virtualLibraries = libraries.GetVirtualFolders();
            var existing = virtualLibraries.Where(
                folder => string.Equals(folder.Name, libraryName, StringComparison.Ordinal)
            ).ToArray();
            if (existing.Length > 1)
                throw new InvalidOperationException("Duplicate VivoAPK library name.");

            if (existing.Length == 0)
            {
                await libraries.AddVirtualFolder(
                    libraryName,
                    CollectionTypeOptions.movies,
                    new LibraryOptions
                    {
                        PathInfos = new[] { new MediaPathInfo(sourceFolder) },
                        EnableRealtimeMonitor = false,
                        SaveLocalMetadata = false,
                    },
                    refreshLibrary: false
                ).ConfigureAwait(false);
                existing = libraries.GetVirtualFolders().Where(
                    folder => string.Equals(folder.Name, libraryName, StringComparison.Ordinal)
                ).ToArray();
            }

            if (existing.Length != 1 ||
                existing[0].Locations.Length != 1 ||
                !string.Equals(
                    Path.GetFullPath(existing[0].Locations[0]),
                    Path.GetFullPath(sourceFolder),
                    StringComparison.Ordinal) ||
                !Guid.TryParse(existing[0].ItemId, out var folderId) ||
                folderId == Guid.Empty)
            {
                throw new InvalidOperationException("Private library does not match the expected user folder.");
            }

            // Set the precise allowed folder; never allow all folders or global tuners.
            user.SetPermission(PermissionKind.EnableAllFolders, false);
            user.SetPreference(PreferenceKind.EnabledFolders, new[] { folderId });
            user.SetPermission(PermissionKind.EnableAllChannels, false);
            user.SetPreference(PreferenceKind.EnabledChannels, Array.Empty<Guid>());
            user.SetPermission(PermissionKind.EnableContentDownloading, false);
            user.SetPermission(PermissionKind.EnablePublicSharing, false);
            await users.UpdateUserAsync(user).ConfigureAwait(false);
        }
        finally
        {
            gate.Release();
        }
    }

    private static void AssertNotLink(string path)
    {
        if (!File.Exists(path) && !Directory.Exists(path))
            return;
        var attributes = File.GetAttributes(path);
        if ((attributes & FileAttributes.ReparsePoint) != 0)
            throw new InvalidOperationException("Refuse linked private library path.");
    }
}
