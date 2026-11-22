using System.Security.Cryptography;
using Jellyfin.Database.Implementations.Entities;
using Jellyfin.Database.Implementations.Enums;
using MediaBrowser.Common;
using MediaBrowser.Controller.Authentication;
using MediaBrowser.Controller.Library;
using MediaBrowser.Model.Cryptography;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.VivoapkMegaAuth;

/// <summary>
/// Validates the existing Mega username/password without creating a second client credential.
/// A successful upstream login creates a restricted Jellyfin identity only if required.
/// </summary>
public sealed class MegaAuthenticationProvider : IAuthenticationProvider, IRequiresResolvedUser
{
    private readonly IApplicationHost _appHost;
    private readonly ICryptoProvider _cryptoProvider;
    private readonly ILogger<MegaAuthenticationProvider> _logger;
    private readonly MegaResolverClient _resolver = new();

    public MegaAuthenticationProvider(
        IApplicationHost appHost,
        ICryptoProvider cryptoProvider,
        ILogger<MegaAuthenticationProvider> logger)
    {
        _appHost = appHost;
        _cryptoProvider = cryptoProvider;
        _logger = logger;
    }

    public string Name => "VivoAPK Mega OTT";

    public bool IsEnabled => Plugin.Instance?.Configuration.Enabled == true;

    public Task<ProviderAuthenticationResult> Authenticate(string username, string password)
        => Authenticate(username, password, null!);

    public async Task<ProviderAuthenticationResult> Authenticate(
        string username, string password, User resolvedUser)
    {
        if (!IsEnabled || string.IsNullOrWhiteSpace(username) || string.IsNullOrWhiteSpace(password))
            throw new AuthenticationException("Invalid username or password.");

        var users = _appHost.Resolve<IUserManager>();
        User? user = resolvedUser ?? users.GetUserByName(username);
        var providerId = GetType().FullName!;

        // Never take over Jellyfin admin/local accounts or identities of another plugin.
        if (user != null && !string.Equals(
            user.AuthenticationProviderId, providerId, StringComparison.Ordinal))
            throw new AuthenticationException("Invalid username or password.");

        // Jellyfin usernames compare case-insensitively. Keep the stored canonical name.
        var canonicalUsername = user?.Username ?? username;
        if (!await _resolver.ValidateAsync(canonicalUsername, password).ConfigureAwait(false))
            throw new AuthenticationException("Invalid username or password.");

        if (user == null)
        {
            user = await users.CreateUserAsync(canonicalUsername).ConfigureAwait(false);
            user.AuthenticationProviderId = providerId;
            user.PasswordResetProviderId = providerId;
            // Do not leave a usable Jellyfin-local password on the generated account.
            user.Password = _cryptoProvider.CreatePasswordHash(
                Convert.ToBase64String(RandomNumberGenerator.GetBytes(64))).ToString();
            user.SetPermission(PermissionKind.IsAdministrator, false);
            user.SetPermission(PermissionKind.EnableAllFolders, false);
            await users.UpdateUserAsync(user).ConfigureAwait(false);
            // Do not log usernames, upstream URLs, or authentication material.
            _logger.LogInformation("Created restricted VivoAPK Jellyfin identity.");
        }

        return new ProviderAuthenticationResult { Username = canonicalUsername };
    }

    public Task ChangePassword(User user, string newPassword)
        => throw new NotSupportedException("Mega OTT manages subscription passwords.");
}
