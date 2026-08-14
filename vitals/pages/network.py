"""Network throughput page with a card per interface."""

from __future__ import annotations

import gi
import psutil

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from .. import theme, ui  # noqa: E402
from ..sampler import Sampler, Snapshot  # noqa: E402
from ..widgets import Chart, Trace  # noqa: E402


class _NicCard(ui.Card):
    def __init__(self, name: str, rx_series, tx_series) -> None:
        super().__init__(name, accent=theme.NET_RX)
        self.state_chip = ui.chip("—")
        self.header_end.append(self.state_chip)
        self.chart = Chart(
            [Trace(rx_series, theme.NET_RX, "down"), Trace(tx_series, theme.NET_TX, "up")],
            percent=False, height=88, formatter=theme.fmt_rate,
        )
        self.chart.baseline_max = 32 * 1024
        self.append(self.chart)
        self.stats = ui.StatGrid(columns=4)
        self.stats.add("rx", "↓ Download")
        self.stats.add("tx", "↑ Upload")
        self.stats.add("rx_total", "Received")
        self.stats.add("tx_total", "Sent")
        self.stats.set_margin_top(4)
        self.append(self.stats)

    def refresh(self, data: dict[str, float], addresses: str) -> None:
        self.chart.queue_draw()
        self.stats.set("rx", theme.fmt_rate(data["rx"]))
        self.stats.set("tx", theme.fmt_rate(data["tx"]))
        self.stats.set("rx_total", theme.fmt_bytes(data["rx_total"]))
        self.stats.set("tx_total", theme.fmt_bytes(data["tx_total"]))
        state = "up" if data["up"] else "down"
        speed = f" · {data['speed']:.0f} Mb/s" if data["speed"] else ""
        self.state_chip.set_label(f"{state}{speed} · {addresses}" if addresses
                                 else f"{state}{speed}")


class NetworkPage(Gtk.Box):
    def __init__(self, sampler: Sampler) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.sampler = sampler
        self.root = ui.page_box()

        total = ui.Card("Total Throughput", accent=theme.NET_RX)
        self.show_all_check = Gtk.CheckButton(label="Show virtual interfaces")
        self.show_all_check.add_css_class("metric-sub")
        self.show_all_check.connect("toggled", lambda *_: self._rebuild())
        total.header_end.append(self.show_all_check)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        left.set_valign(Gtk.Align.CENTER)
        left.set_size_request(132, -1)
        self.rx_label = Gtk.Label(label="↓ 0 B/s", xalign=0)
        self.rx_label.add_css_class("metric-mid")
        self.tx_label = Gtk.Label(label="↑ 0 B/s", xalign=0)
        self.tx_label.add_css_class("metric-mid")
        left.append(self.rx_label)
        left.append(self.tx_label)
        row.append(left)
        self.total_chart = Chart(
            [Trace(sampler.hist_rx, theme.NET_RX, "down"),
             Trace(sampler.hist_tx, theme.NET_TX, "up")],
            percent=False, height=128, formatter=theme.fmt_rate,
        )
        self.total_chart.baseline_max = 64 * 1024
        row.append(self.total_chart)
        total.append(row)

        self.total_stats = ui.StatGrid(columns=4)
        self.total_stats.add("rx_total", "Total received")
        self.total_stats.add("tx_total", "Total sent")
        self.total_stats.add("peak_rx", "Peak download")
        self.total_stats.add("peak_tx", "Peak upload")
        self.total_stats.set_margin_top(6)
        total.append(self.total_stats)
        self.root.append(total)

        self.nic_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.root.append(self.nic_container)
        self.cards: dict[str, _NicCard] = {}
        self.append(ui.scroll(self.root))

    def _rebuild(self) -> None:
        for card in self.cards.values():
            self.nic_container.remove(card)
        self.cards.clear()

    def _visible(self, name: str) -> bool:
        if self.show_all_check.get_active():
            return True
        return Sampler.is_physical_nic(name)

    @staticmethod
    def _addresses(name: str) -> str:
        try:
            addrs = psutil.net_if_addrs().get(name, [])
        except OSError:
            return ""
        for addr in addrs:
            if addr.family.name == "AF_INET":
                return addr.address
        return ""

    def update(self, s: Snapshot) -> None:
        self.total_chart.queue_draw()
        self.rx_label.set_label(f"↓ {theme.fmt_rate(s.net_rx_rate)}")
        self.tx_label.set_label(f"↑ {theme.fmt_rate(s.net_tx_rate)}")
        self.total_stats.set("rx_total", theme.fmt_bytes(s.net_rx_total))
        self.total_stats.set("tx_total", theme.fmt_bytes(s.net_tx_total))
        self.total_stats.set("peak_rx", theme.fmt_rate(self.sampler.hist_rx.peak()))
        self.total_stats.set("peak_tx", theme.fmt_rate(self.sampler.hist_tx.peak()))

        for name, data in sorted(s.per_nic.items()):
            if not self._visible(name):
                continue
            card = self.cards.get(name)
            if card is None:
                series = self.sampler.hist_nic.get(name)
                if series is None:
                    continue
                card = _NicCard(name, series[0], series[1])
                self.cards[name] = card
                self.nic_container.append(card)
            card.refresh(data, self._addresses(name))
