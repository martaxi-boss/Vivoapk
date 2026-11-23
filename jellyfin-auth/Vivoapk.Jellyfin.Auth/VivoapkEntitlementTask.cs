using Jellyfin.Data;
using MediaBrowser.Common.Configuration;
using Jellyfin.Database.Implementations.Enums;
using MediaBrowser.Controller.Library;
using MediaBrowser.Model.Tasks;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.VivoapkMegaAuth;

/// <summary>
/// Scheduled Jellyfin task: revalidates only identities created by this plugin.
/// A definite Mega expiry or disabled status blocks further Jellyfin API access;
/// transient upstream failures never disable users. It never touches admins.
/// Disabled by plugin configuration unless the operator opts in after tests.
/// </summary>
public sealed class VivoapkEntitlementTask : IScheduledTask
{
    private readonly IUserManager _users;
    private readonly ILogger<VivoapkEntitlementTask> _logger;
    private readonly MegaResolverClient _resolver = new();
    private readonly VivoapkSuspensionMarker _suspensions;

    public VivoapkEntitlementTask(
        IUserManager users,
        IApplicationPaths appPaths,
        ILogger<VivoapkEntitlementTask> logger)
    {
        _users = users;
        _logger = logger;
        _suspensions = new VivoapkSuspensionMarker(appPaths);
    }

    public string Name => "VivoAPK — verificar subscrições Mega OTT";
    public string Description => "Bloqueia clientes VivoAPK expirados, mantendo as contas administrativas.";
    public string Category => "VivoAPK";
    public string Key => "VivoapkMegaEntitlementMonitor";
    public bool IsHidden => false;
    public bool IsEnabled => true;
    public bool IsLogged => true;

    public IEnumerable<TaskTriggerInfo> GetDefaultTriggers()
    {
        yield return new TaskTriggerInfo
        {
            Type = TaskTriggerInfoType.IntervalTrigger,
            IntervalTicks = TimeSpan.FromMinutes(5).Ticks
        };
    }

    public async Task ExecuteAsync(IProgress<double> progress, CancellationToken cancellationToken)
    {
        if (Plugin.Instance?.Configuration is not { Enabled: true, MonitorEntitlements: true })
        {
            progress.Report(100);
            return;
        }

        var providerId = typeof(MegaAuthenticationProvider).FullName;
        var ownedUsers = _users.GetUsers()
            .Where(user => string.Equals(
                user.AuthenticationProviderId, providerId, StringComparison.Ordinal)
                && !user.HasPermission(PermissionKind.IsAdministrator))
            .ToArray();

        var blocked = 0;
        var restored = 0;
        var checkedCount = 0;
        foreach (var user in ownedUsers)
        {
            cancellationToken.ThrowIfCancellationRequested();
            var ownedSuspension = _suspensions.IsOwned(user.Id);
            // Administrator-initiated disabled accounts have NO plugin marker
            // and are never modified or automatically reactivated.
            if (user.HasPermission(PermissionKind.IsDisabled) && !ownedSuspension)
                continue;

            var status = await _resolver.CheckEntitlementAsync(
                user.Username, cancellationToken).ConfigureAwait(false);
            checkedCount++;
            var current = _users.GetUserById(user.Id);
            if (current is null
                || !string.Equals(current.AuthenticationProviderId, providerId,
                    StringComparison.Ordinal)
                || current.HasPermission(PermissionKind.IsAdministrator))
                continue;

            if (status is EntitlementState.Expired or EntitlementState.Disabled)
            {
                if (!current.HasPermission(PermissionKind.IsDisabled))
                {
                    // Write the durable ownership marker BEFORE disabling.
                    // If Jellyfin update fails, undo the marker and report failure.
                    _suspensions.Mark(current.Id);
                    try
                    {
                        current.SetPermission(PermissionKind.IsDisabled, true);
                        await _users.UpdateUserAsync(current).ConfigureAwait(false);
                        blocked++;
                    }
                    catch
                    {
                        _suspensions.Clear(current.Id);
                        throw;
                    }
                }
            }
            else if (status == EntitlementState.Active && ownedSuspension)
            {
                // Only undo a disable that this exact plugin previously created.
                // Never re-enable a disabled admin or a manually disabled user.
                if (current.HasPermission(PermissionKind.IsDisabled))
                {
                    current.SetPermission(PermissionKind.IsDisabled, false);
                    await _users.UpdateUserAsync(current).ConfigureAwait(false);
                    restored++;
                }
                _suspensions.Clear(current.Id);
            }
            // UNAVAILABLE: preserve existing state; do not change another account.
            progress.Report(ownedUsers.Length == 0
                ? 100 : 100.0 * checkedCount / ownedUsers.Length);
            // Bound the rate at which the provider control API is polled.
            await Task.Delay(TimeSpan.FromMilliseconds(250), cancellationToken)
                .ConfigureAwait(false);
        }
        progress.Report(100);
        _logger.LogInformation(
            "VivoAPK entitlement check finished: {Checked} checked, {Blocked} blocked, " +
            "{Restored} renewed.",
            checkedCount, blocked, restored);
    }
}
