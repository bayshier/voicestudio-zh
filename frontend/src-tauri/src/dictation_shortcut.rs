//! One registration seam for the native global-shortcut plugin and the
//! Wayland GlobalShortcuts portal.

use std::str::FromStr;
use std::sync::atomic::Ordering;
use std::sync::Mutex;

use serde::Serialize;
use tauri::{Emitter, Manager};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut};

use crate::TrayHandle;

#[derive(Clone, Debug, Serialize)]
pub struct ShortcutInfo {
    pub accelerator: String,
    pub display: String,
    pub backend: &'static str,
}

pub struct DictationShortcutManager {
    updates: Mutex<()>,
    native: Mutex<Option<Shortcut>>,
    effective: Mutex<ShortcutInfo>,
    #[cfg(target_os = "linux")]
    portal: crate::wayland_shortcut::PortalShortcutState,
}

impl DictationShortcutManager {
    pub fn new(accelerator: &str) -> Self {
        Self {
            updates: Mutex::new(()),
            native: Mutex::new(None),
            effective: Mutex::new(ShortcutInfo {
                accelerator: accelerator.to_owned(),
                display: display_accelerator(accelerator),
                backend: backend_name(),
            }),
            #[cfg(target_os = "linux")]
            portal: crate::wayland_shortcut::PortalShortcutState::default(),
        }
    }

    pub fn register_initial(app: tauri::AppHandle, accelerator: String) {
        let accelerator = match Shortcut::from_str(&accelerator) {
            Ok(_) => accelerator,
            Err(error) => {
                let fallback = crate::config::default_dictation_shortcut();
                log::warn!(
                    "Saved shortcut '{accelerator}' is invalid ({error}); using '{fallback}'"
                );
                fallback
            }
        };
        #[cfg(target_os = "linux")]
        if crate::wayland_shortcut::is_wayland_session() {
            let revision = app.state::<Self>().portal.reserve();
            crate::wayland_shortcut::register_initial(app, accelerator, revision);
            return;
        }

        let manager = app.state::<Self>();
        match manager.replace_native(&app, &accelerator) {
            Ok(()) => {
                manager.publish(&app, accelerator, None, "native");
            }
            Err(error) => log::warn!("Failed to register global shortcut: {error}"),
        }
    }

    pub fn replace(
        &self,
        app: &tauri::AppHandle,
        accelerator: &str,
    ) -> Result<ShortcutInfo, String> {
        Shortcut::from_str(accelerator)
            .map_err(|error| format!("Invalid shortcut '{accelerator}': {error}"))?;

        #[cfg(target_os = "linux")]
        if crate::wayland_shortcut::is_wayland_session() {
            let display = self.portal.replace(app.clone(), accelerator.to_owned())?;
            return Ok(self.publish(app, accelerator.to_owned(), Some(display), "portal"));
        }

        self.replace_native(app, accelerator)?;
        Ok(self.publish(app, accelerator.to_owned(), None, "native"))
    }

    pub fn info(&self) -> ShortcutInfo {
        self.effective
            .lock()
            .map(|info| info.clone())
            .unwrap_or_else(|_| ShortcutInfo {
                accelerator: String::new(),
                display: String::new(),
                backend: backend_name(),
            })
    }

    pub fn serialize_update<T>(
        &self,
        update: impl FnOnce() -> Result<T, String>,
    ) -> Result<T, String> {
        let _guard = self
            .updates
            .lock()
            .map_err(|_| "shortcut update lock poisoned".to_string())?;
        update()
    }

    #[cfg(target_os = "linux")]
    pub(crate) fn register_portal_initial(
        &self,
        app: &tauri::AppHandle,
        accelerator: String,
        revision: u64,
    ) -> Result<(), String> {
        Shortcut::from_str(&accelerator)
            .map_err(|error| format!("Invalid shortcut '{accelerator}': {error}"))?;
        let display = self
            .portal
            .replace_reserved(app.clone(), accelerator.clone(), revision)?;
        self.publish(app, accelerator, Some(display), "portal");
        Ok(())
    }

    fn replace_native(&self, app: &tauri::AppHandle, accelerator: &str) -> Result<(), String> {
        let parsed = Shortcut::from_str(accelerator)
            .map_err(|error| format!("Invalid shortcut '{accelerator}': {error}"))?;
        let global = app.global_shortcut();
        let mut slot = self
            .native
            .lock()
            .map_err(|_| "shortcut lock poisoned".to_string())?;
        if let Some(shortcut) = slot.as_ref() {
            global.unregister(shortcut.clone()).map_err(|error| {
                format!("Failed to unregister the previous shortcut: {error}")
            })?;
        }
        let previous = slot.take();
        if let Err(error) = global.register(parsed.clone()) {
            if let Some(shortcut) = previous {
                if global.register(shortcut.clone()).is_ok() {
                    *slot = Some(shortcut);
                }
            }
            return Err(format!("Failed to register '{accelerator}': {error}"));
        }
        *slot = Some(parsed);
        Ok(())
    }

    fn publish(
        &self,
        app: &tauri::AppHandle,
        accelerator: String,
        display: Option<String>,
        backend: &'static str,
    ) -> ShortcutInfo {
        let info = ShortcutInfo {
            display: display.unwrap_or_else(|| display_accelerator(&accelerator)),
            accelerator,
            backend,
        };
        if let Ok(mut current) = self.effective.lock() {
            *current = info.clone();
        }
        let recording = app
            .try_state::<crate::AppFlags>()
            .is_some_and(|flags| flags.dictating.load(Ordering::SeqCst));
        update_tray_hint(app, &info.display, recording);
        let _ = app.emit("dictation-shortcut-changed", &info);
        log::info!(
            "Dictation shortcut '{}' active through {}",
            info.accelerator,
            info.backend
        );
        info
    }
}

pub fn update_tray_hint(app: &tauri::AppHandle, display: &str, recording: bool) {
    let verb = if recording { "Stop" } else { "Start" };
    if let Ok(slot) = app.state::<TrayHandle>().dictate.lock() {
        if let Some(item) = slot.as_ref() {
            if let Err(error) = item.set_text(format!("{verb} Dictation  {display}")) {
                log::warn!("Could not update the dictation tray hint: {error}");
            }
        }
    }
}

pub fn display_accelerator(accelerator: &str) -> String {
    #[cfg(target_os = "macos")]
    {
        return accelerator
            .split('+')
            .map(|part| match part.to_ascii_lowercase().as_str() {
                "cmdorctrl" | "commandorcontrol" | "cmd" | "command" | "meta" | "super" => {
                    "⌘".to_owned()
                }
                "ctrl" | "control" => "⌃".to_owned(),
                "alt" | "option" => "⌥".to_owned(),
                "shift" => "⇧".to_owned(),
                _ => part.to_owned(),
            })
            .collect::<String>();
    }
    #[cfg(not(target_os = "macos"))]
    accelerator
        .split('+')
        .map(|part| match part.to_ascii_lowercase().as_str() {
            "cmdorctrl" | "commandorcontrol" | "ctrl" | "control" => "Ctrl",
            "cmd" | "command" | "meta" | "super" => "Super",
            "alt" | "option" => "Alt",
            "shift" => "Shift",
            _ => part,
        })
        .collect::<Vec<_>>()
        .join("+")
}

fn backend_name() -> &'static str {
    // The focused-window bridge is available before the OS registration
    // completes and remains the truthful fallback if that registration fails.
    "focused"
}

#[cfg(test)]
mod tests {
    use super::{display_accelerator, DictationShortcutManager};
    use std::sync::{mpsc, Arc, Barrier, Mutex};
    use std::time::Duration;

    #[test]
    fn formats_the_platform_shortcut_hint() {
        #[cfg(target_os = "macos")]
        assert_eq!(display_accelerator("CmdOrCtrl+Shift+Space"), "⌘⇧Space");
        #[cfg(not(target_os = "macos"))]
        assert_eq!(
            display_accelerator("CmdOrCtrl+Shift+Space"),
            "Ctrl+Shift+Space"
        );
        #[cfg(not(target_os = "macos"))]
        assert_eq!(display_accelerator("Cmd+Option+K"), "Super+Alt+K");
    }

    #[test]
    fn serializes_overlapping_update_and_rollback_flows() {
        let manager = Arc::new(DictationShortcutManager::new("Ctrl+Shift+Space"));
        let state = Arc::new(Mutex::new((
            "Ctrl+Shift+Space".to_string(),
            "Ctrl+Shift+Space".to_string(),
        )));
        let (first_entered_tx, first_entered_rx) = mpsc::channel();
        let (release_first_tx, release_first_rx) = mpsc::channel();
        let (second_entered_tx, second_entered_rx) = mpsc::channel();
        let second_ready = Arc::new(Barrier::new(2));

        let first_manager = Arc::clone(&manager);
        let first_state = Arc::clone(&state);
        let first = std::thread::spawn(move || {
            first_manager
                .serialize_update(|| {
                    first_state.lock().unwrap().1 = "Ctrl+Alt+A".into();
                    first_entered_tx.send(()).unwrap();
                    release_first_rx.recv().unwrap();
                    // Simulate a failed persistence and its runtime rollback.
                    first_state.lock().unwrap().1 = "Ctrl+Shift+Space".into();
                    Err::<(), _>("disk full".to_string())
                })
                .unwrap_err()
        });
        first_entered_rx.recv().unwrap();

        let second_manager = Arc::clone(&manager);
        let second_state = Arc::clone(&state);
        let second_barrier = Arc::clone(&second_ready);
        let second = std::thread::spawn(move || {
            second_barrier.wait();
            second_manager
                .serialize_update(|| {
                    second_entered_tx.send(()).unwrap();
                    let mut state = second_state.lock().unwrap();
                    state.1 = "Ctrl+Alt+B".into();
                    state.0 = "Ctrl+Alt+B".into();
                    Ok(())
                })
                .unwrap();
        });
        second_ready.wait();
        assert!(second_entered_rx
            .recv_timeout(Duration::from_millis(50))
            .is_err());

        release_first_tx.send(()).unwrap();
        assert_eq!(first.join().unwrap(), "disk full");
        second.join().unwrap();
        let state = state.lock().unwrap();
        assert_eq!(state.0, "Ctrl+Alt+B");
        assert_eq!(state.1, "Ctrl+Alt+B");
    }
}
