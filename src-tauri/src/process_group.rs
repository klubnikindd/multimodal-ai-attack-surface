//! Unix lifecycle (§11.2.2): новая process group + PDEATHSIG (Linux).
//! Windows-аналог — job_object.rs (Job Object kill-on-close).

#![cfg(unix)]

use std::process::Command;

/// Настроить Command: новая process group (setpgid 0) + PDEATHSIG на Linux.
pub fn configure(cmd: &mut Command) {
    use std::os::unix::process::CommandExt;
    cmd.process_group(0);
    #[cfg(target_os = "linux")]
    unsafe {
        cmd.pre_exec(|| {
            if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGTERM as libc::c_ulong) == -1 {
                return Err(std::io::Error::last_os_error());
            }
            if libc::getppid() == 1 {
                libc::kill(libc::getpid(), libc::SIGTERM);
            }
            Ok(())
        });
    }
}

/// SIGTERM всей process group (sidecar + внуки; pgid = pid sidecar).
pub fn terminate_group(pid: u32) {
    unsafe {
        libc::kill(-(pid as i32), libc::SIGTERM);
    }
}

/// SIGKILL всей process group.
pub fn kill_group(pid: u32) {
    unsafe {
        libc::kill(-(pid as i32), libc::SIGKILL);
    }
}
