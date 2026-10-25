/** Describe only known server-selected vaults; never infer protection from OS/UA. */
export function credentialProtectionCopy(backend: unknown) {
  switch (backend) {
    case "dpapi_file":
      return { configured: true, text: "Saved credentials use Windows DPAPI protection bound to your Windows user account." };
    case "linux_secret_service":
      return { configured: true, text: "Saved credentials use your desktop’s Linux Secret Service. An unlocked keyring is required to save or use them." };
    default:
      return { configured: false, text: "Secure credential storage is unavailable. Check your operating system’s credential storage before saving a key." };
  }
}
