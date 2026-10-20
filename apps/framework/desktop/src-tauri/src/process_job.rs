// Only the worker created by this host is assigned; unrelated applications are never adopted.
pub struct OwnedJob {
    #[cfg(windows)]
    handle: isize,
}

impl OwnedJob {
    pub fn attach(pid: u32) -> Result<Self, String> {
        #[cfg(windows)]
        unsafe {
            use windows_sys::Win32::{
                Foundation::CloseHandle,
                System::{
                    JobObjects::*,
                    Threading::{OpenProcess, PROCESS_SET_QUOTA, PROCESS_TERMINATE},
                },
            };
            let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if job.is_null() {
                return Err(std::io::Error::last_os_error().to_string());
            }
            let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            if SetInformationJobObject(
                job,
                JobObjectExtendedLimitInformation,
                &limits as *const _ as *const _,
                std::mem::size_of_val(&limits) as u32,
            ) == 0
            {
                CloseHandle(job);
                return Err(std::io::Error::last_os_error().to_string());
            }
            let process = OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, 0, pid);
            if process.is_null() {
                CloseHandle(job);
                return Err(std::io::Error::last_os_error().to_string());
            }
            let assigned = AssignProcessToJobObject(job, process);
            let error = std::io::Error::last_os_error();
            CloseHandle(process);
            if assigned == 0 {
                CloseHandle(job);
                return Err(error.to_string());
            }
            let owned = Self {
                handle: job as isize,
            };
            // The worker was created suspended. Establish ownership before any
            // Python launcher or future tool can create descendants.
            resume_initial_thread(pid)?;
            Ok(owned)
        }
        #[cfg(not(windows))]
        {
            let _ = pid;
            Ok(Self {})
        }
    }
}

#[cfg(windows)]
unsafe fn resume_initial_thread(pid: u32) -> Result<(), String> {
    use windows_sys::Win32::{
        Foundation::{CloseHandle, INVALID_HANDLE_VALUE},
        System::{
            Diagnostics::ToolHelp::{
                CreateToolhelp32Snapshot, Thread32First, Thread32Next, TH32CS_SNAPTHREAD,
                THREADENTRY32,
            },
            Threading::{OpenThread, ResumeThread, THREAD_SUSPEND_RESUME},
        },
    };
    let snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
    if snapshot == INVALID_HANDLE_VALUE {
        return Err(std::io::Error::last_os_error().to_string());
    }
    let mut entry: THREADENTRY32 = std::mem::zeroed();
    entry.dwSize = std::mem::size_of::<THREADENTRY32>() as u32;
    let mut present = Thread32First(snapshot, &mut entry);
    let mut thread_id = None;
    while present != 0 {
        if entry.th32OwnerProcessID == pid {
            thread_id = Some(entry.th32ThreadID);
            break;
        }
        present = Thread32Next(snapshot, &mut entry);
    }
    CloseHandle(snapshot);
    let thread_id = thread_id.ok_or("Owned suspended worker thread was not found")?;
    let thread = OpenThread(THREAD_SUSPEND_RESUME, 0, thread_id);
    if thread.is_null() {
        return Err(std::io::Error::last_os_error().to_string());
    }
    let previous_count = ResumeThread(thread);
    let error = std::io::Error::last_os_error();
    CloseHandle(thread);
    if previous_count == u32::MAX {
        return Err(error.to_string());
    }
    if previous_count != 1 {
        return Err("Unexpected owned worker suspension state".into());
    }
    Ok(())
}

impl Drop for OwnedJob {
    fn drop(&mut self) {
        #[cfg(windows)]
        unsafe {
            windows_sys::Win32::Foundation::CloseHandle(self.handle as _);
        }
    }
}
