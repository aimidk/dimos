"""SubApp base class for DUI sub-applications."""

from __future__ import annotations

from textual.widget import Widget


class SubApp(Widget):
    """Base class for DUI sub-applications.

    Each sub-app is a Widget that renders inside a display pane.
    Subclasses must set TITLE and implement compose().

    Lifecycle:
      - on_mount_subapp() is called exactly ONCE after the widget's
        children have been composed.  Heavy / blocking work (LCM
        connections, etc.) should be dispatched via self.run_worker().
      - on_unmount_subapp() is called when the DUI app is shutting down,
        NOT on every tab switch.
    """

    TITLE: str = "Untitled"

    can_focus = False

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._subapp_initialized = False

    @property
    def has_focus(self) -> bool:
        """True if the currently focused widget is inside this sub-app."""
        focused = self.app.focused
        if focused is None:
            return False
        # Walk up the DOM tree to see if focused widget is a descendant
        node = focused
        while node is not None:
            if node is self:
                return True
            node = node.parent
        return False

    def get_focus_target(self) -> Widget | None:
        """Return the widget that should receive focus for this sub-app.

        Override in subclasses for custom focus logic.
        Default: first visible focusable descendant.
        """
        for child in self.query("*"):
            if child.can_focus and child.display and child.styles.display != "none":
                return child
        return None

    def on_mount(self) -> None:
        """Textual lifecycle — fires after compose() children exist."""
        if not self._subapp_initialized:
            self._subapp_initialized = True
            self.on_mount_subapp()

    def on_mount_subapp(self) -> None:
        """Called exactly once after first mount.

        Override to start LCM subscriptions, timers, etc.
        Heavy / blocking work should use ``self.run_worker()``.
        """

    def on_unmount_subapp(self) -> None:
        """Called when the DUI app tears down this sub-app.

        Override to stop LCM subscriptions, timers, etc.
        """
