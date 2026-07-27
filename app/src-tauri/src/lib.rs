// Mac DLNA Cast — shell Tauri (fase C1).
//
// La app es un cascarón alrededor del servidor Python compilado (sidecar):
// lo lanza al abrir, muestra el HUD (servido por el propio sidecar en
// 127.0.0.1:8200) en la ventana principal, y lo mata en CUALQUIER vía de
// salida — lección de la fase B, donde el quit del Dock dejaba el server
// huérfano. Cerrar la ventana NO cierra la app (convención macOS): sigue
// viva en el Dock/tray sirviendo al TV; Salir de verdad va por el menú del
// tray o el Dock (⌘Q).

use std::sync::Mutex;

use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager, RunEvent, WindowEvent,
};
use tauri_plugin_shell::{process::CommandChild, ShellExt};

struct ServerChild(Mutex<Option<CommandChild>>);

fn media_dir() -> String {
    // Por ahora: ~/Movies (o el home si no existe). Elegir carpeta desde la
    // app llega en C3; el HUD ya permite navegar la biblioteca.
    let home = std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .unwrap_or_else(|_| ".".into());
    let movies = std::path::Path::new(&home).join("Movies");
    if movies.is_dir() {
        movies.to_string_lossy().into_owned()
    } else {
        home
    }
}

fn spawn_server(app: &tauri::AppHandle) {
    let state = app.state::<ServerChild>();
    let mut guard = state.0.lock().unwrap();
    if guard.is_some() {
        return;
    }
    match app.shell().sidecar("mac-dlna-cast-server") {
        // El watchdog del server vigila este stdin: si la app muere (quit,
        // crash, kill), el pipe se cierra y el server se apaga solo.
        Ok(cmd) => match cmd
            .args([media_dir()])
            .env("DLNA_EXIT_ON_STDIN_EOF", "1")
            .spawn()
        {
            Ok((_rx, child)) => *guard = Some(child),
            Err(e) => eprintln!("[app] no pude lanzar el server: {e}"),
        },
        Err(e) => eprintln!("[app] sidecar no encontrado: {e}"),
    }
}

fn kill_server(app: &tauri::AppHandle) {
    let state = app.state::<ServerChild>();
    let child = state.0.lock().unwrap().take();
    if let Some(child) = child {
        let _ = child.kill();
    }
}

fn show_main_window(app: &tauri::AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.set_focus();
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            // Segundo lanzamiento → enfocar la instancia viva, no duplicar.
            show_main_window(app);
        }))
        .manage(ServerChild(Mutex::new(None)))
        .setup(|app| {
            spawn_server(app.handle());

            let abrir = MenuItem::with_id(app, "abrir", "Abrir panel", true, None::<&str>)?;
            let salir =
                MenuItem::with_id(app, "salir", "Salir (detiene el servidor)", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&abrir, &salir])?;
            TrayIconBuilder::with_id("tray")
                .icon(app.default_window_icon().unwrap().clone())
                .icon_as_template(true)
                .menu(&menu)
                .show_menu_on_left_click(true)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "abrir" => show_main_window(app),
                    "salir" => app.exit(0),
                    _ => {}
                })
                .build(app)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            // Cerrar la ventana la oculta; la app (y el server) siguen vivos.
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .build(tauri::generate_context!())
        .expect("error building tauri app")
        .run(|app, event| match event {
            // Reabrir desde el Dock → mostrar la ventana (evento solo macOS).
            #[cfg(target_os = "macos")]
            RunEvent::Reopen { .. } => show_main_window(app),
            // Cualquier salida real (⌘Q, menú del tray) mata el server.
            RunEvent::ExitRequested { .. } => kill_server(app),
            RunEvent::Exit => kill_server(app),
            _ => {}
        });
}
