from __future__ import annotations

import argparse
import sys
from pathlib import Path

import gi

gi.require_version("IBus", "1.0")
from gi.repository import GLib, IBus

from .engine import ArabiziEngine

BUS_NAME = "org.freedesktop.IBus.Arabizi"
ENGINE_NAME = "arabizi-translit-engine"


def _component_xml(exec_path: str) -> str:
    return f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<component>
  <name>{BUS_NAME}</name>
  <description>Arabizi to Arabic transliteration engine</description>
  <exec>{exec_path} --ibus</exec>
  <version>0.1.0</version>
  <author>Arabizi-Translit-Engine</author>
  <license>MIT</license>
  <homepage>https://example.local/arabizi-ibus</homepage>
  <textdomain>arabizi-ibus</textdomain>
  <engines>
    <engine>
      <name>{ENGINE_NAME}</name>
      <longname>Arabizi Transliteration</longname>
      <description>Real-time Arabizi to Arabic transliteration</description>
      <language>ar</language>
      <license>MIT</license>
      <author>Arabizi-Translit-Engine</author>
      <icon></icon>
      <layout>us</layout>
      <rank>80</rank>
      <symbol>عر</symbol>
      <setup></setup>
    </engine>
  </engines>
</component>
"""


class IMEApplication:
    def __init__(self) -> None:
        IBus.init()
        self.loop = GLib.MainLoop()
        self.bus = IBus.Bus()
        self.bus.connect("disconnected", self._on_disconnected)

        self.factory = IBus.Factory.new(self.bus.get_connection())
        self.factory.add_engine(ENGINE_NAME, ArabiziEngine.__gtype__)

    def run(self, ibus_mode: bool) -> None:
        if ibus_mode:
            self.bus.request_name(BUS_NAME, 0)
        self.loop.run()

    def _on_disconnected(self, bus: IBus.Bus) -> None:
        del bus
        self.loop.quit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Arabizi IBus engine")
    parser.add_argument("--ibus", action="store_true", help="Run engine under IBus")
    parser.add_argument("--xml", action="store_true", help="Print component XML")
    parser.add_argument(
        "--exec-path",
        default=f"/usr/bin/python3 {Path(__file__).resolve()}",
        help="Executable path to place inside component XML",
    )

    args = parser.parse_args(argv)

    if args.xml:
        print(_component_xml(args.exec_path))
        return 0

    app = IMEApplication()
    app.run(ibus_mode=args.ibus)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
