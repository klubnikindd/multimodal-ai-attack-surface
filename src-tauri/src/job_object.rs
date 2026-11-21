//! Windows Job Object с KILL_ON_JOB_CLOSE (SEC-17, §11.2.2):
//! если оболочка умирает (в т.ч. kill -9), ОС добивает всё дерево sidecar
//! (engine + внуки exiftool/ffmpeg/llama-server).

#![cfg(windows)]

use windows::Win32::Foundation::{CloseHandle, HANDLE};
use windows::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
    SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};
use windows::Win32::System::Threading::{OpenProcess, PROCESS_SET_QUOTA, PROCESS_TERMINATE};

/// Job Object, чей handle убивает дерево процессов при закрытии последнего дескриптора.
pub struct JobObject(HANDLE);

impl JobObject {
    /// Создаёт job с флагом KILL_ON_JOB_CLOSE.
    pub fn new_kill_on_close() -> Result<Self, String> {
        unsafe {
            let handle = CreateJobObjectW(None, None)
                .map_err(|e| format!("CreateJobObjectW failed: {e}"))?;
            let mut info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            let size = std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32;
            let result = SetInformationJobObject(
                handle,
                JobObjectExtendedLimitInformation,
                &info as *const _ as *const core::ffi::c_void,
                size,
            );
            if let Err(e) = result {
                let _ = CloseHandle(handle);
                return Err(format!("SetInformationJobObject failed: {e}"));
            }
            Ok(JobObject(handle))
        }
    }

    /// Назначает уже запущенный процесс (и его будущих детей) в job.
    pub fn assign(&self, pid: u32) -> Result<(), String> {
        unsafe {
            let process = OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, false, pid)
                .map_err(|e| format!("OpenProcess({pid}) failed: {e}"))?;
            let result = AssignProcessToJobObject(self.0, process);
            let _ = CloseHandle(process);
            result.map_err(|e| format!("AssignProcessToJobObject failed: {e}"))
        }
    }
}

impl Drop for JobObject {
    fn drop(&mut self) {
        unsafe {
            let _ = CloseHandle(self.0);
        }
    }
}

// HANDLE безопасно пересылать между потоками (это дескриптор ОС, не указатель на память).
unsafe impl Send for JobObject {}
unsafe impl Sync for JobObject {}
