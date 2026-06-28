"""Settings UI screens for Meeting Notes."""

from typing import Optional
import subprocess
from textual.app import ComposeResult
from textual.containers import Container, Vertical, Horizontal, ScrollableContainer
from textual.widgets import Static, Label, Button, Input, Select, ListView, ListItem
from textual.screen import Screen, ModalScreen
from textual.reactive import reactive
from textual import work

from meeting_notes.config import AppConfig, save_config, validate_config, get_config_path
from meeting_notes.ai_models import PROVIDERS
from meeting_notes.ollama_utils import (
    get_installed_models,
    get_recommended_models,
    install_model,
    check_ollama_installed,
)


class InstallingModelScreen(ModalScreen):
    """Screen shown while installing an Ollama model."""

    CSS = """
    InstallingModelScreen {
        align: center middle;
    }
    
    #installing-dialog {
        width: 70;
        height: auto;
        border: thick $primary;
        background: $surface;
        padding: 2;
    }
    
    #installing-title {
        text-align: center;
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }
    
    #installing-status {
        text-align: center;
        color: $text;
        margin: 1 0;
    }
    
    #installing-progress {
        text-align: center;
        color: $text-muted;
        margin: 1 0;
    }
    
    #installing-spinner {
        text-align: center;
        color: $accent;
        margin: 1 0;
    }
    """

    status_message = reactive("Starting...")
    progress_percentage = reactive(0)

    def __init__(self, model_name: str, **kwargs):
        super().__init__(**kwargs)
        self.model_name = model_name
        self.spinner_frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        self.spinner_index = 0

    def compose(self) -> ComposeResult:
        with Container(id="installing-dialog"):
            yield Static(f"Installing {self.model_name}", id="installing-title")
            yield Static("", id="installing-status")
            yield Static("", id="installing-progress")
            yield Static("", id="installing-spinner")

    def on_mount(self) -> None:
        """Start the installation and spinner animation."""
        self.update_spinner()
        self.start_installation()

    def watch_status_message(self, message: str) -> None:
        """Update status message when changed."""
        self.query_one("#installing-status", Static).update(message)

    def watch_progress_percentage(self, percentage: int) -> None:
        """Update progress when changed."""
        if percentage > 0:
            bar_width = 40
            filled = int(bar_width * percentage / 100)
            bar = "█" * filled + "░" * (bar_width - filled)
            self.query_one("#installing-progress", Static).update(f"{bar} {percentage}%")

    def update_spinner(self) -> None:
        """Update spinner animation."""
        spinner = self.spinner_frames[self.spinner_index]
        self.query_one("#installing-spinner", Static).update(f"{spinner} Please wait...")
        self.spinner_index = (self.spinner_index + 1) % len(self.spinner_frames)
        self.set_timer(0.1, self.update_spinner)

    @work(exclusive=True, thread=True)
    def start_installation(self) -> None:
        """Install the model in a background thread."""
        try:

            def progress_callback(status: str, percentage: int):
                self.call_from_thread(setattr, self, "status_message", status)
                self.call_from_thread(setattr, self, "progress_percentage", percentage)

            success = install_model(self.model_name, progress_callback)

            if success:
                self.call_from_thread(self.dismiss, True)
            else:
                self.call_from_thread(self.dismiss, False)

        except Exception as e:
            error_msg = str(e)
            self.call_from_thread(self.dismiss, False)
            # TODO: Show error to user


class InstallModelScreen(ModalScreen[str]):
    """Modal for selecting and installing a new Ollama model."""

    CSS = """
    InstallModelScreen {
        align: center middle;
    }
    
    #install-dialog {
        width: 80;
        height: auto;
        border: thick $primary;
        background: $surface;
        padding: 2;
    }
    
    #install-title {
        text-align: center;
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }
    
    #install-list {
        height: 15;
        margin: 1 0;
    }
    
    #install-buttons {
        width: 100%;
        height: auto;
        align: center middle;
        margin-top: 1;
    }
    
    .install-button {
        margin: 0 1;
    }
    
    ListItem {
        padding: 1;
    }
    
    ListItem:hover {
        background: $boost;
    }
    """

    def __init__(self, installed_models: list, **kwargs):
        super().__init__(**kwargs)
        self.installed_models = [m.name for m in installed_models]
        self.selected_model = None

    def compose(self) -> ComposeResult:
        with Container(id="install-dialog"):
            yield Static("📦 Install Ollama Model", id="install-title")
            yield Static("Select a model to install:", id="install-subtitle")

            with ListView(id="install-list"):
                for model_info in get_recommended_models():
                    name = model_info["name"]
                    desc = model_info["description"]
                    size = model_info["size"]
                    is_recommended = model_info["recommended"]
                    is_installed = name in self.installed_models

                    star = "⭐" if is_recommended else "  "
                    installed_mark = " ✓ (installed)" if is_installed else ""
                    label_text = f"{star} {name} - {desc} ({size}){installed_mark}"

                    item = ListItem(Label(label_text))
                    item.model_name = name  # Store model name
                    item.is_installed = is_installed
                    yield item

            with Horizontal(id="install-buttons"):
                yield Button("Cancel", variant="default", id="cancel-button", classes="install-button")
                yield Button("Install", variant="primary", id="install-button", classes="install-button")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle model selection."""
        if hasattr(event.item, "model_name"):
            self.selected_model = event.item.model_name

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "install-button":
            if self.selected_model:
                # Check if already installed
                list_view = self.query_one("#install-list", ListView)
                if list_view.highlighted_child and hasattr(list_view.highlighted_child, "is_installed"):
                    if list_view.highlighted_child.is_installed:
                        self.app.notify("Model is already installed", severity="warning")
                        return

                self.dismiss(self.selected_model)
            else:
                self.app.notify("Please select a model first", severity="warning")
        else:
            self.dismiss(None)


class SettingsScreen(Screen):
    """Full-screen settings interface."""

    CSS = """
    SettingsScreen {
        background: $surface;
    }
    
    #settings-container {
        width: 100%;
        height: 100%;
        layout: horizontal;
    }
    
    #settings-sidebar {
        width: 25%;
        height: 100%;
        border-right: solid $primary;
        padding: 1;
    }
    
    #settings-content-wrapper {
        width: 75%;
        height: 100%;
        layout: vertical;
    }
    
    #settings-content {
        width: 100%;
        height: 1fr;
        padding: 2;
        overflow-y: auto;
    }
    
    #settings-footer {
        width: 100%;
        height: 5;
        padding: 1 2;
        background: $surface;
        border-top: solid $primary;
    }
    
    .sidebar-item {
        padding: 1;
        margin-bottom: 1;
    }
    
    .sidebar-item:hover {
        background: $boost;
    }
    
    .sidebar-item.-active {
        background: $accent;
        color: $text;
    }
    
    .settings-section-title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
        margin-top: 2;
    }
    
    .settings-field {
        margin-bottom: 2;
    }
    
    .settings-label {
        color: $text-muted;
        margin-bottom: 1;
    }
    
    .settings-input {
        width: 100%;
    }
    
    .settings-hint {
        color: $text-muted;
        text-style: italic;
        margin-top: 1;
    }
    
    #model-list {
        margin: 1 0;
        max-height: 10;
    }
    
    #settings-buttons {
        width: 100%;
        align: center middle;
    }
    
    .settings-action-button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("v", "view_config_file", "View Config File"),
    ]

    def __init__(self, config: AppConfig, **kwargs):
        super().__init__(**kwargs)
        self.config = config.to_dict()  # Work with a copy
        self.original_config = config
        self.current_section = "ai"

    def compose(self) -> ComposeResult:
        with Container(id="settings-container"):
            # Sidebar
            with Vertical(id="settings-sidebar"):
                yield Static("⚙️  Settings", classes="settings-section-title")
                yield Button("AI Models", id="section-ai", classes="sidebar-item -active")
                yield Button("Directories", id="section-dirs", classes="sidebar-item")
                yield Button("Audio", id="section-audio", classes="sidebar-item")
                yield Button("Editor", id="section-editor", classes="sidebar-item")

            # Content area wrapper (scrollable content + fixed footer)
            with Vertical(id="settings-content-wrapper"):
                # Scrollable content
                with ScrollableContainer(id="settings-content"):
                    yield from self.render_ai_section()

                # Fixed footer with action buttons
                with Container(id="settings-footer"):
                    with Horizontal(id="settings-buttons"):
                        yield Button("Cancel", variant="default", id="cancel-button", classes="settings-action-button")
                        yield Button("Save", variant="primary", id="save-button", classes="settings-action-button")

    def render_ai_section(self) -> list:
        """Render AI Models section."""
        widgets = []

        widgets.append(Static("🤖 AI Summarization", classes="settings-section-title"))

        # AI Provider Selection
        widgets.append(Static("AI Provider", classes="settings-label"))

        current_provider = self.config.get("ai_provider", "none")

        for provider_id, provider in PROVIDERS.items():
            is_current = provider_id == current_provider
            marker = "●" if is_current else "○"
            label = f"{marker} {provider.label}"
            btn = Button(label, id=f"provider-{provider_id}", variant="primary" if is_current else "default")
            btn.provider_id = provider_id
            widgets.append(btn)
            if is_current:
                widgets.append(Static(f"  → {provider.description}", classes="settings-hint"))

        widgets.append(Static(""))  # Spacer

        # Show provider-specific settings
        provider_spec = PROVIDERS.get(current_provider)
        if provider_spec and provider_spec.models:
            widgets.extend(self.render_cloud_provider_settings(current_provider))
        elif current_provider == "local":
            widgets.extend(self.render_local_ollama_settings())
        elif current_provider == "none":
            widgets.append(Static("✓ No AI summarization - transcripts only", classes="settings-hint"))

        # Whisper Model (Transcription)
        widgets.append(Static(""))  # Spacer
        widgets.append(Static("Whisper Model (Transcription)", classes="settings-label"))
        widgets.append(Static(f"Current: {self.config.get('whisper_model', 'base')}", classes="settings-hint"))
        widgets.append(Static("(Model selection coming soon)", classes="settings-hint"))

        return widgets

    def render_cloud_provider_settings(self, provider_id: str) -> list:
        """Render settings for a cloud AI provider from the provider registry."""
        widgets = []
        provider = PROVIDERS[provider_id]

        widgets.append(Static(f"{provider.label} Settings", classes="settings-section-title"))

        # Model selection
        widgets.append(Static("Model", classes="settings-label"))
        current_model = self.config.get("ai_model", provider.default_model)

        for model_id, model in provider.models.items():
            is_current = model_id == current_model
            marker = "●" if is_current else "○"
            btn = Button(
                f"{marker} {model['name']}", id=f"aimodel-{model_id}", variant="primary" if is_current else "default"
            )
            btn.model_id = model_id
            widgets.append(btn)
            if is_current:
                widgets.append(Static(f"  → {model['description']}", classes="settings-hint"))

        # API Key
        widgets.append(Static("API Key", classes="settings-label"))
        api_key = self.config.get(provider.api_key_field, "") if provider.api_key_field else ""
        key_input = Input(
            value=api_key,
            password=True,
            id=f"{provider_id}-key-input",
            classes="settings-input",
            placeholder=f"or set {provider.env_var} env var",
        )
        widgets.append(key_input)
        widgets.append(Static("💡 Tip: Use environment variable for security", classes="settings-hint"))

        return widgets

    def render_local_ollama_settings(self) -> list:
        """Render local Ollama settings."""
        widgets = []

        widgets.append(Static("Local Ollama Settings", classes="settings-section-title"))
        widgets.append(Static("⚠️  Warning: Very slow (10+ min per meeting)", classes="settings-hint"))
        widgets.append(Static("⚠️  High CPU usage (400%), not recommended", classes="settings-hint"))

        widgets.append(Static("Ollama Model", classes="settings-label"))

        try:
            installed = get_installed_models()
            if not installed:
                widgets.append(Static("⚠️  No Ollama models installed!", classes="settings-hint"))
                widgets.append(Button("Install a Model", variant="warning", id="install-model-button"))
            else:
                # Show installed models with current selection
                # `or` fallback handles empty-string config values, which
                # dict.get's default does not.
                current = self.config.get("ollama_model") or PROVIDERS["local"].default_model

                for model in installed:
                    is_current = model.name == current
                    marker = "●" if is_current else "○"
                    label = f"{marker} {model.name}"
                    if model.size:
                        label += f" ({model.size})"

                    # Sanitize model name for ID (replace invalid chars with hyphens)
                    safe_id = model.name.replace(":", "-").replace(".", "-")
                    btn = Button(label, id=f"model-{safe_id}", variant="primary" if is_current else "default")
                    btn.model_name = model.name
                    widgets.append(btn)

                widgets.append(Button("+ Install New Model", variant="default", id="install-model-button"))

        except Exception as e:
            widgets.append(Static(f"⚠️  Error loading models: {e}", classes="settings-hint"))

        return widgets

    def render_directories_section(self) -> list:
        """Render Directories section."""
        widgets = []
        config = AppConfig.from_dict(self.config)

        widgets.append(Static("📁 Directories", classes="settings-section-title"))

        widgets.append(Static("Notes Directory", classes="settings-label"))
        notes_input = Input(value=self.config["notes_dir"], id="notes-dir-input", classes="settings-input")
        widgets.append(notes_input)
        widgets.append(
            Static(
                "• Relative path (e.g., 'notes') or absolute path (e.g., '/home/user/notes')", classes="settings-hint"
            )
        )
        widgets.append(Static("• Directory must already exist", classes="settings-hint"))
        widgets.append(
            Static(
                f"• Current resolves to: {config.resolved_path('notes_dir')}",
                classes="settings-hint",
            )
        )

        widgets.append(Static("Recordings Directory", classes="settings-label"))
        rec_input = Input(value=self.config["recordings_dir"], id="rec-dir-input", classes="settings-input")
        widgets.append(rec_input)
        widgets.append(Static("• Relative path (e.g., 'recordings') or absolute path", classes="settings-hint"))
        widgets.append(Static("• Directory must already exist", classes="settings-hint"))
        widgets.append(
            Static(
                f"• Current resolves to: {config.resolved_path('recordings_dir')}",
                classes="settings-hint",
            )
        )

        widgets.append(Static("Transcripts Directory", classes="settings-label"))
        trans_input = Input(value=self.config["transcripts_dir"], id="trans-dir-input", classes="settings-input")
        widgets.append(trans_input)
        widgets.append(Static("• Relative path (e.g., 'transcripts') or absolute path", classes="settings-hint"))
        widgets.append(Static("• Directory must already exist", classes="settings-hint"))
        widgets.append(
            Static(
                f"• Current resolves to: {config.resolved_path('transcripts_dir')}",
                classes="settings-hint",
            )
        )

        return widgets

    def render_audio_section(self) -> list:
        """Render Audio section."""
        widgets = []

        widgets.append(Static("🎤 Audio Settings", classes="settings-section-title"))

        widgets.append(Static("Recording Mode", classes="settings-label"))
        # TODO: Add proper Select widget once implemented
        widgets.append(Static(f"Current: {self.config['recording_mode']}", classes="settings-hint"))
        widgets.append(Static("(Mode selection coming soon)", classes="settings-hint"))

        return widgets

    def render_editor_section(self) -> list:
        """Render Editor section."""
        widgets = []

        widgets.append(Static("✏️  Editor", classes="settings-section-title"))

        widgets.append(Static("Preferred Editor", classes="settings-label"))
        editor_input = Input(value=self.config["editor"], id="editor-input", classes="settings-input")
        widgets.append(editor_input)
        widgets.append(Static("Command used to open notes (e.g. nvim, code, emacs)", classes="settings-hint"))

        widgets.append(Static("Terminal File Browser", classes="settings-label"))
        browser_input = Input(
            value=self.config.get("terminal_file_browser", ""),
            id="terminal-file-browser-input",
            classes="settings-input",
        )
        widgets.append(browser_input)
        widgets.append(
            Static(
                "Terminal file browser for browsing notes (e.g. ranger, vidir, nnn, lf, vifm, yazi)",
                classes="settings-hint",
            )
        )
        return widgets

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        button_id = event.button.id

        # Section navigation
        if button_id and button_id.startswith("section-"):
            section = button_id.split("-")[1]
            await self.switch_section(section)

        # Provider selection
        elif button_id and button_id.startswith("provider-"):
            if hasattr(event.button, "provider_id"):
                provider_id = event.button.provider_id
                self.config["ai_provider"] = provider_id
                # Set default model for provider
                if provider_id in PROVIDERS and PROVIDERS[provider_id].models:
                    self.config["ai_model"] = PROVIDERS[provider_id].default_model
                elif provider_id == "local":
                    # `or` fallback so an empty-string ollama_model still
                    # produces a runnable default (dict.get only fills in the
                    # default when the key is missing entirely).
                    self.config["ai_model"] = self.config.get("ollama_model") or PROVIDERS["local"].default_model
                await self.refresh_content()

        # AI model selection (for cloud providers)
        elif button_id and button_id.startswith("aimodel-"):
            if hasattr(event.button, "model_id"):
                self.config["ai_model"] = event.button.model_id
                await self.refresh_content()

        # Ollama model selection (for local provider)
        elif button_id and button_id.startswith("model-"):
            if hasattr(event.button, "model_name"):
                self.config["ollama_model"] = event.button.model_name
                self.config["ai_model"] = event.button.model_name  # Sync ai_model
                await self.refresh_content()

        # Install model
        elif button_id == "install-model-button":
            self.action_install_model()

        # Save/Cancel
        elif button_id == "save-button":
            self.action_save()
        elif button_id == "cancel-button":
            self.action_cancel()

    async def switch_section(self, section: str) -> None:
        """Switch to a different settings section."""
        self.current_section = section

        # Update sidebar highlights
        for item in self.query(".sidebar-item"):
            if item.id == f"section-{section}":
                item.add_class("-active")
            else:
                item.remove_class("-active")

        await self.refresh_content()

    async def refresh_content(self) -> None:
        """Refresh the content area based on current section."""
        content = self.query_one("#settings-content", ScrollableContainer)
        await content.remove_children()

        # Render appropriate section
        if self.current_section == "ai":
            widgets = self.render_ai_section()
        elif self.current_section == "dirs":
            widgets = self.render_directories_section()
        elif self.current_section == "audio":
            widgets = self.render_audio_section()
        elif self.current_section == "editor":
            widgets = self.render_editor_section()
        else:
            widgets = []

        # Mount all widgets (buttons are now in fixed footer, not here)
        for widget in widgets:
            await content.mount(widget)

    def action_install_model(self) -> None:
        """Open the install model dialog."""
        try:
            installed = get_installed_models()
            self.app.push_screen(InstallModelScreen(installed), self.handle_install_model)
        except Exception as e:
            self.app.notify(f"Error: {e}", severity="error")

    def handle_install_model(self, model_name: Optional[str]) -> None:
        """Handle model installation."""
        if model_name:
            # Show installing screen
            self.app.push_screen(InstallingModelScreen(model_name), self.handle_installation_complete)

    async def handle_installation_complete(self, success: bool) -> None:
        """Handle completion of model installation."""
        if success:
            self.app.notify("✓ Model installed successfully!", severity="information")
            # Refresh the content to show new model
            await self.refresh_content()
        else:
            self.app.notify("✗ Model installation failed", severity="error")

    def action_save(self) -> None:
        """Save settings and close."""
        # Update config from inputs only if the current settings section is mounted.
        for selector, key in (
            ("#notes-dir-input", "notes_dir"),
            ("#rec-dir-input", "recordings_dir"),
            ("#trans-dir-input", "transcripts_dir"),
            ("#editor-input", "editor"),
            ("#terminal-file-browser-input", "terminal_file_browser"),
            ("#openai-key-input", "openai_api_key"),
            ("#anthropic-key-input", "anthropic_api_key"),
            ("#openrouter-key-input", "openrouter_api_key"),
        ):
            field = self.query(selector)
            if field:
                self.config[key] = field[0].value.strip()

        # Create config object and validate
        new_config = AppConfig.from_dict(self.config)
        valid, error = validate_config(new_config)

        if not valid:
            self.app.notify(f"✗ {error}", severity="error", timeout=10)
            return

        # Save to file
        try:
            save_config(new_config)
            self.dismiss(new_config)
        except Exception as e:
            self.app.notify(f"✗ Failed to save: {e}", severity="error")

    def action_cancel(self) -> None:
        """Cancel and close without saving."""
        self.dismiss(None)

    def action_view_config_file(self) -> None:
        """Open the config file in the configured editor."""
        config_path = get_config_path()
        editor = self.config.get("editor", "nvim")

        try:
            # Open in editor
            subprocess.Popen([editor, str(config_path)])
            self.app.notify(f"Opening config in {editor}: {config_path}", severity="information")
        except Exception as e:
            self.app.notify(f"Failed to open config: {e}", severity="error")
