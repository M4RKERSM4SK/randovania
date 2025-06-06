from __future__ import annotations

import logging
import typing

from PySide6 import QtGui, QtWidgets

from randovania.game.game_enum import RandovaniaGame

if typing.TYPE_CHECKING:
    from randovania.gui.main_window import MainWindow
    from randovania.gui.widgets.base_game_tab_widget import BaseGameTabWidget
    from randovania.interface_common.options import Options
    from randovania.layout.versioned_preset import VersionedPreset


class GamesHelpWidget(QtWidgets.QTabWidget):
    _main_window: MainWindow
    _last_options: Options
    _first_show: bool = True
    _experimental_visible: bool = False
    _index_for_game: dict[RandovaniaGame, int] | None = None
    _layout_for_index: dict[int, QtWidgets.QVBoxLayout] | None = None
    _widget_for_game: dict[RandovaniaGame, BaseGameTabWidget] | None = None
    _pending_current_game: RandovaniaGame | None = None
    _pending_select_preset_tab: bool = False

    def _on_first_show(self):
        self._index_for_game = {}
        self._layout_for_index = {}
        self._widget_for_game = {}

        self.tabBar().setVisible(False)
        self.tabBar().setDocumentMode(True)

        for game in RandovaniaGame.sorted_all_games():
            widget = QtWidgets.QWidget()
            widget.game = game
            widget_layout = QtWidgets.QVBoxLayout(widget)
            widget_layout.setContentsMargins(0, 0, 0, 0)

            index = self.addTab(widget, game.long_name)
            self.setTabVisible(index, game.data.development_state.can_view())
            self._index_for_game[game] = index
            self._layout_for_index[index] = widget_layout

        if self._pending_current_game is not None:
            self.set_current_game(self._pending_current_game)
            self._pending_current_game = None

        self.currentChanged.connect(self.ensure_current_game_has_widget)
        self.ensure_current_game_has_widget()

    def set_main_window(self, window):
        self._main_window = window

    def current_game(self) -> RandovaniaGame | None:
        if self._index_for_game is not None:
            for game, index in self._index_for_game.items():
                if index == self.currentIndex():
                    return game
        return None

    def current_game_widget(self) -> BaseGameTabWidget | None:
        if self._widget_for_game is not None:
            return self._widget_for_game.get(self.current_game())
        return None

    def ensure_current_game_has_widget(self):
        game = self.current_game()
        if game is not None and game not in self._widget_for_game:
            logging.info("Creating game tab for %s", game.value)
            new_tab = game.gui.game_tab(self._main_window, self._main_window, self._last_options)
            logging.info("Game tab created")
            self._widget_for_game[game] = new_tab
            self._layout_for_index[self.currentIndex()].addWidget(new_tab)
            new_tab.on_options_changed(self._last_options)
            logging.info("Game tab updated for options")

    def showEvent(self, arg: QtGui.QShowEvent) -> None:
        if self._first_show:
            self._first_show = False
            self._on_first_show()

        return super().showEvent(arg)

    def set_current_game(self, game: RandovaniaGame):
        if self._first_show:
            self._pending_current_game = game
        else:
            self.setCurrentIndex(self._index_for_game[game])
            if self._pending_select_preset_tab:
                self.current_game_widget().select_preset_tab()
                self._pending_select_preset_tab = False

    def on_options_changed(self, options: Options):
        self._last_options = options
        if self._widget_for_game is not None:
            for widget in self._widget_for_game.values():
                widget.on_options_changed(options)

    def on_new_preset(self, preset: VersionedPreset) -> None:
        if self._widget_for_game is not None:
            widget = self._widget_for_game.get(preset.game)
            if widget is not None:
                widget.tab_generate_game.on_new_preset(preset)

    def select_preset_tab(self) -> None:
        """Sets the current widget for the selected game to be"""
        widget = self.current_game_widget()
        if widget is not None:
            widget.select_preset_tab()
        else:
            self._pending_select_preset_tab = True
