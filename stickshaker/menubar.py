"""Menu bar icon: the yoke, with Enabled/Disabled, a last-event line, and Quit.

Cocoa must own the main thread, so the caller starts the socket server and
watcher on background threads before calling run(). UI updates from other
threads go through AppHelper.callAfter, which runs them on the main thread.
"""

import signal
from datetime import datetime
from pathlib import Path

import objc
from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSControlStateValueOff,
    NSControlStateValueOn,
    NSEvent,
    NSEventTypeApplicationDefined,
    NSImage,
    NSMenu,
    NSMenuItem,
    NSStatusBar,
    NSVariableStatusItemLength,
)
from Foundation import NSObject, NSPoint, NSSize
from PyObjCTools import AppHelper, MachSignals

from stickshaker import state
from stickshaker.events import Event
from stickshaker.player import Player

ICON_PATH = Path(__file__).resolve().parent.parent / "assets" / "stickshaker.svg"
ICON_POINTS = 18


class MenuBar(NSObject):
    def initWithPlayer_(self, player: Player):
        self = objc.super(MenuBar, self).init()
        if self is None:
            return None
        self.player = player
        item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        icon = NSImage.alloc().initWithContentsOfFile_(str(ICON_PATH))
        if icon is not None:
            icon.setSize_(NSSize(ICON_POINTS, ICON_POINTS))
            icon.setTemplate_(True)  # macOS tints it for light and dark menu bars
            item.button().setImage_(icon)
        else:
            item.button().setTitle_("stickshaker")
        item.button().setToolTip_("stickshaker")

        menu = NSMenu.alloc().init()
        self.toggle = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Enabled", "toggle:", "")
        self.toggle.setTarget_(self)
        menu.addItem_(self.toggle)
        self.last = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("No sounds yet", None, "")
        self.last.setEnabled_(False)
        menu.addItem_(self.last)
        menu.addItem_(NSMenuItem.separatorItem())
        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Quit stickshaker", "quit:", "q")
        quit_item.setTarget_(self)
        menu.addItem_(quit_item)
        item.setMenu_(menu)
        self.item = item  # keep a reference, or the icon disappears
        self.refresh()
        return self

    def refresh(self) -> None:
        on = self.player.enabled
        self.toggle.setState_(NSControlStateValueOn if on else NSControlStateValueOff)
        self.item.button().setAppearsDisabled_(not on)

    def toggle_(self, _sender) -> None:
        self.player.enabled = not self.player.enabled
        state.set_enabled(self.player.enabled)
        self.refresh()

    def quit_(self, _sender) -> None:
        stop()

    def showEvent_(self, event: Event) -> None:
        when = datetime.now().strftime("%H:%M")
        self.last.setTitle_(f"Last: {event.source} {event.kind}, {event.size:,} bytes, {when}")


def stop() -> None:
    """Make NSApp.run() return, so the caller's cleanup runs.

    AppHelper.stopEventLoop() would call NSApp.terminate_, which exits the
    process directly and skips the cleanup (the socket file stays behind).
    stop_ takes effect after the next event, so post an empty one.
    """
    app = NSApplication.sharedApplication()
    app.stop_(None)
    wake = NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
        NSEventTypeApplicationDefined, NSPoint(0, 0), 0, 0, 0, None, 0, 0, 0
    )
    app.postEvent_atStart_(wake, True)


def run(player: Player) -> None:
    """Show the icon and run the Cocoa event loop until Quit, Ctrl-C or SIGTERM."""
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)  # no Dock icon
    bar = MenuBar.alloc().initWithPlayer_(player)
    player.on_played = lambda ev: AppHelper.callAfter(bar.showEvent_, ev)
    # Python signal handlers don't run while Cocoa owns the thread. MachSignals
    # delivers signals through the run loop instead. (AppHelper.runEventLoop's
    # installInterrupt only works when NSApp doesn't exist yet; we create it
    # above to hide the Dock icon.)
    for signum in (signal.SIGINT, signal.SIGTERM):
        MachSignals.signal(signum, lambda _signum: stop())
    app.run()
