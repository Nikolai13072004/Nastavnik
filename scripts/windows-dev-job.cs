using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;

// Windows-only resource envelope for local development, not application code.
public static class WindowsDevJob
{
    private const uint ActiveProcessLimit = 0x00000008;
    private const uint JobMemoryLimit = 0x00000200;
    private const uint KillOnJobClose = 0x00002000;

    [StructLayout(LayoutKind.Sequential)]
    private struct BasicLimits
    {
        public long ProcessUserTime, JobUserTime;
        public uint Flags;
        public UIntPtr MinimumWorkingSet, MaximumWorkingSet;
        public uint ActiveProcesses;
        public UIntPtr Affinity;
        public uint PriorityClass, SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct IoCounters
    {
        public ulong ReadOperations, WriteOperations, OtherOperations;
        public ulong ReadBytes, WriteBytes, OtherBytes;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ExtendedLimits
    {
        public BasicLimits Basic;
        public IoCounters Io;
        public UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct CpuLimits
    {
        public uint Flags, Rate;
    }

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern IntPtr CreateJobObject(IntPtr attributes, string name);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetInformationJobObject(IntPtr job, int infoClass, IntPtr info, uint size);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

    [DllImport("kernel32.dll")]
    private static extern bool CloseHandle(IntPtr handle);

    private static void SetLimits<T>(IntPtr job, int infoClass, T limits) where T : struct
    {
        int size = Marshal.SizeOf(typeof(T));
        IntPtr buffer = Marshal.AllocHGlobal(size);
        try
        {
            Marshal.StructureToPtr(limits, buffer, false);
            if (!SetInformationJobObject(job, infoClass, buffer, (uint)size))
                throw new Win32Exception(Marshal.GetLastWin32Error());
        }
        finally
        {
            Marshal.FreeHGlobal(buffer);
        }
    }

    public static IntPtr CreateForCurrentProcess()
    {
        IntPtr job = CreateJobObject(IntPtr.Zero, null);
        if (job == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
        try
        {
            SetLimits(job, 9, new ExtendedLimits
            {
                Basic = new BasicLimits
                {
                    Flags = ActiveProcessLimit | JobMemoryLimit | KillOnJobClose,
                    ActiveProcesses = 12
                },
                JobMemory = new UIntPtr(4UL * 1024 * 1024 * 1024)
            });
            // ENABLE | HARD_CAP. Rate is in hundredths of one percent.
            SetLimits(job, 15, new CpuLimits { Flags = 0x1 | 0x4, Rate = 2000 });
            using (Process current = Process.GetCurrentProcess())
            {
                if (!AssignProcessToJobObject(job, current.Handle))
                    throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            // Intentionally owned by the dedicated launcher until it exits.
            return job;
        }
        catch
        {
            CloseHandle(job);
            throw;
        }
    }
}
