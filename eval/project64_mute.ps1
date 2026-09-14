# Mute every Windows audio session owned by one process (Volume Mixer's per-app
# mute), re-checking until that process exits. Changes nothing inside the
# emulator. Prints one line per session it mutes.
#   powershell -NoProfile -ExecutionPolicy Bypass -File project64_mute.ps1 -ProcessId 1234
param([Parameter(Mandatory = $true)][int]$ProcessId)

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator {
    [PreserveSig] int EnumAudioEndpoints(int dataFlow, int stateMask, out IMMDeviceCollection devices);
}
[Guid("0BD7A1BE-7A1A-44DB-8397-CC5392387B5E"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceCollection {
    [PreserveSig] int GetCount(out int count);
    [PreserveSig] int Item(int index, out IMMDevice device);
}
[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice {
    [PreserveSig] int Activate(ref Guid iid, int clsCtx, IntPtr activationParams,
                               [MarshalAs(UnmanagedType.IUnknown)] out object instance);
}
[Guid("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioSessionManager2 {
    [PreserveSig] int GetAudioSessionControl(IntPtr groupingParam, int flags, out IntPtr control);
    [PreserveSig] int GetSimpleAudioVolume(IntPtr groupingParam, int flags, out IntPtr volume);
    [PreserveSig] int GetSessionEnumerator(out IAudioSessionEnumerator sessions);
}
[Guid("E2F5BB11-0570-40CA-ACDD-3AA01277DEE8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioSessionEnumerator {
    [PreserveSig] int GetCount(out int count);
    [PreserveSig] int GetSession(int index, out IAudioSessionControl2 session);
}
[Guid("BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioSessionControl2 {
    [PreserveSig] int GetState(out int state);
    [PreserveSig] int GetDisplayName(IntPtr name);
    [PreserveSig] int SetDisplayName(IntPtr name, IntPtr context);
    [PreserveSig] int GetIconPath(IntPtr path);
    [PreserveSig] int SetIconPath(IntPtr path, IntPtr context);
    [PreserveSig] int GetGroupingParam(IntPtr param);
    [PreserveSig] int SetGroupingParam(IntPtr param, IntPtr context);
    [PreserveSig] int RegisterAudioSessionNotification(IntPtr client);
    [PreserveSig] int UnregisterAudioSessionNotification(IntPtr client);
    [PreserveSig] int GetSessionIdentifier(IntPtr id);
    [PreserveSig] int GetSessionInstanceIdentifier(IntPtr id);
    [PreserveSig] int GetProcessId(out uint pid);
}
[Guid("87CE5498-68D6-44E5-9215-6DA47EF883D8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface ISimpleAudioVolume {
    [PreserveSig] int SetMasterVolume(float level, ref Guid context);
    [PreserveSig] int GetMasterVolume(out float level);
    [PreserveSig] int SetMute(bool mute, ref Guid context);
    [PreserveSig] int GetMute(out bool mute);
}
[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDeviceEnumerator {}

public static class ProcessMute {
    public static int Mute(uint pid) {
        var enumerator = (IMMDeviceEnumerator)new MMDeviceEnumerator();
        IMMDeviceCollection devices;
        enumerator.EnumAudioEndpoints(0, 1, out devices);        // render, active
        int deviceCount; devices.GetCount(out deviceCount);
        int muted = 0;
        Guid managerId = typeof(IAudioSessionManager2).GUID, context = Guid.Empty;
        for (int d = 0; d < deviceCount; d++) {
            IMMDevice device; devices.Item(d, out device);
            object instance; device.Activate(ref managerId, 23, IntPtr.Zero, out instance);
            IAudioSessionEnumerator sessions;
            ((IAudioSessionManager2)instance).GetSessionEnumerator(out sessions);
            int count; sessions.GetCount(out count);
            for (int i = 0; i < count; i++) {
                IAudioSessionControl2 session; sessions.GetSession(i, out session);
                uint owner; session.GetProcessId(out owner);
                if (owner != pid) continue;
                var volume = (ISimpleAudioVolume)session;
                bool already; volume.GetMute(out already);
                if (!already) { volume.SetMute(true, ref context); muted++; }
            }
        }
        return muted;
    }
}
'@

while (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue) {
    try {
        $count = [ProcessMute]::Mute([uint32]$ProcessId)
        if ($count -gt 0) { Write-Output "muted $count session(s) of process $ProcessId" }
    } catch {
        Write-Output "mute error: $($_.Exception.Message)"
    }
    Start-Sleep -Milliseconds 500
}
