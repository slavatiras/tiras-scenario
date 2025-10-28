# -*- coding: utf-8 -*-
import sys
import uuid
import logging
from lxml import etree as ET # Залишаємо для clipboard
from PyQt6.QtWidgets import (
    QMainWindow, QGraphicsScene, QDockWidget, QListWidget, QWidget,
    QLabel, QLineEdit, QFormLayout, QFileDialog, QTextEdit,
    QVBoxLayout, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QHBoxLayout, QComboBox, QMessageBox, QListWidgetItem,
    QApplication, QToolBar, QTabWidget, QSpinBox, QGridLayout, QCheckBox,
    QScrollArea, QInputDialog, QSpacerItem, QSizePolicy, QStackedWidget, QFrame # <-- Додано QFrame
)
from PyQt6.QtGui import QColor, QAction, QUndoStack, QFont, QIcon
from PyQt6.QtCore import Qt, QTimer, QPointF # Додано QPointF

# --- НОВІ ІМПОРТИ ---
from project_manager import ProjectManager, DEVICE_SPECS # Імпортуємо менеджер та константи пристроїв
from serialization import import_project_data, export_project_data # Функції імпорту/експорту
from validation import validate_scenario_on_scene, validate_macro_on_scene # Функції валідації
from clipboard import copy_selection_to_clipboard, paste_selection_from_clipboard # Функції буферу обміну
from scene_utils import populate_scene_from_data, extract_data_from_scene # Функції для роботи зі сценою
# --- КІНЕЦЬ НОВИХ ІМПОРТІВ ---

from nodes import (BaseNode, Connection, CommentItem, FrameItem, NODE_REGISTRY, TriggerNode,
                   ActivateOutputNode, DeactivateOutputNode, DelayNode, SendSMSNode,
                   ConditionNodeZoneState, RepeatNode, SequenceNode, MacroNode,
                   MacroInputNode, MacroOutputNode)
# Команди імпортуються там, де вони потрібні
from editor_view import EditorView
from simulator import ScenarioSimulator
from constants import EDIT_MODE_SCENARIO, EDIT_MODE_MACRO

log = logging.getLogger(__name__)

# DEVICE_SPECS тепер імпортується з project_manager

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        log.debug("Initializing MainWindow...")
        self.setWindowTitle("Редактор сценаріїв Tiras")
        self.setGeometry(100, 100, 1400, 900)

        # --- ЗАМІНА project_data на project_manager ---
        self.project_manager = ProjectManager()
        # self.project_data більше не використовується напряму
        # --- КІНЕЦЬ ЗАМІНИ ---

        self.active_scenario_id = None
        self.active_macro_id = None
        self.current_edit_mode = EDIT_MODE_SCENARIO
        self.previous_scenario_id = None
        self.current_selected_node = None
        self._old_scenario_name = None # Залишаємо для обробки QListWidget.itemChanged
        # self._old_macro_name = None # Більше не потрібен, використовуємо rename_macro

        self.undo_stack = QUndoStack(self)
        self.props_apply_timer = QTimer(self) # Таймер для властивостей залишається тут
        self.props_apply_timer.setSingleShot(True)
        self.props_apply_timer.setInterval(750)
        self.props_apply_timer.timeout.connect(self.on_apply_button_clicked) # Обробник властивостей тут

        self.scene = QGraphicsScene()
        self.scene.setBackgroundBrush(QColor("#333"))
        self.view = EditorView(self.scene, self.undo_stack, self)
        self.simulator = ScenarioSimulator(self.scene, self) # Симулятор залишається тут
        self.setCentralWidget(self.view)

        # --- ЗМІНА: Додано прапорці для контролю оновлень під час завантаження ---
        self._loading_project = False
        self._initializing = False
        # --- КІНЕЦЬ ЗМІНИ ---

        self._create_actions()
        self._create_menu_bar()
        # --- ЗМІНА: Змінено порядок створення панелей інструментів ---
        self._create_simulation_toolbar() # Створюємо спочатку
        self._create_toolbars() # Потім створюємо/оновлюємо інші (динамічні)
        # --- КІНЕЦЬ ЗМІНИ ---
        self._create_panels() # Цей метод значно спроститься

        self.scene.selectionChanged.connect(self.on_selection_changed)
        # --- ЗАМІНА: Використовуємо _trigger_validation замість прямої валідації ---
        self.undo_stack.indexChanged.connect(self._handle_undo_redo) # Перейменовано обробник
        # --- КІНЕЦЬ ЗАМІНИ ---
        # self.undo_stack.indexChanged.connect(self._update_simulation_trigger_zones) # Симуляція залишається тут # ВИДАЛЕНО - дублюючий виклик

        # --- ДОДАНО: Підключення сигналу від ProjectManager до оновлення UI ---
        self.project_manager.project_updated.connect(self._handle_project_update) # Перейменовано обробник
        log.debug("Connected project_manager.project_updated signal.")
        # --- КІНЕЦЬ ДОДАНОГО ---

        self.new_project() # Викликаємо метод ініціалізації

        self.statusBar().showMessage("Готово")
        self._update_window_title()
        log.debug("MainWindow initialized.")

    # --- ЗМІНА: Нові обробники сигналів для контролю оновлень ---
    def _handle_project_update(self):
        """Обробник сигналу project_updated від ProjectManager."""
        log.debug("Received project_updated signal.")
        if self._loading_project or self._initializing:
            log.debug("  Skipping UI update due to loading/initializing flag.")
            return
        self.update_ui_from_project()

    def _handle_undo_redo(self):
        """Обробник сигналу indexChanged від QUndoStack."""
        log.debug("Undo stack index changed.")
        if self._loading_project or self._initializing:
            log.debug("  Skipping validation/sim update due to loading/initializing flag.")
            return
        self._trigger_validation()
        self._update_simulation_trigger_zones() # Оновлюємо зони симуляції після undo/redo
    # --- КІНЕЦЬ ЗМІНИ ---

    def _trigger_validation(self):
        """Запускає валідацію з невеликою затримкою."""
        if self._loading_project or self._initializing: # Додано перевірку прапорців
            log.debug("Scheduling validation check skipped (loading/initializing).")
            return
        log.debug("Scheduling validation check...") # Діагностика
        QTimer.singleShot(1, self.validate_current_view)

    def _update_window_title(self):
        base_title = "Редактор сценаріїв Tiras"
        if self.current_edit_mode == EDIT_MODE_MACRO and self.active_macro_id:
            # --- ВИКОРИСТАННЯ project_manager ---
            macro_data = self.project_manager.get_macro_data(self.active_macro_id)
            macro_name = macro_data.get('name', self.active_macro_id) if macro_data else self.active_macro_id
            # --- КІНЕЦЬ ---
            self.setWindowTitle(f"{base_title} - [Макрос: {macro_name}]")
        elif self.current_edit_mode == EDIT_MODE_SCENARIO and self.active_scenario_id:
            self.setWindowTitle(f"{base_title} - [{self.active_scenario_id}]")
        else:
            self.setWindowTitle(base_title)

    # --- Actions, Menus, Toolbars ---
    def _create_actions(self):
        self.new_action = QAction("&Новий проект", self)
        self.new_action.triggered.connect(self.new_project) # Викликає метод MainWindow
        self.import_action = QAction("&Імпорт...", self)
        self.import_action.triggered.connect(self.import_project) # Викликає метод MainWindow
        self.export_action = QAction("&Експорт...", self)
        self.export_action.triggered.connect(self.export_project) # Викликає метод MainWindow
        self.undo_action = self.undo_stack.createUndoAction(self, "&Скасувати")
        self.undo_action.setShortcut("Ctrl+Z")
        self.redo_action = self.undo_stack.createRedoAction(self, "&Повторити")
        self.redo_action.setShortcut("Ctrl+Y")
        self.copy_action = QAction("&Копіювати", self)
        self.copy_action.setShortcut("Ctrl+C")
        self.copy_action.triggered.connect(self.copy_selection) # Викликає метод MainWindow
        self.paste_action = QAction("&Вставити", self)
        self.paste_action.setShortcut("Ctrl+V")
        self.paste_action.triggered.connect(self.paste_at_center) # Викликає метод MainWindow
        self.add_comment_action = QAction("Додати коментар", self)
        self.add_comment_action.triggered.connect(self.add_comment) # Викликає метод MainWindow
        self.back_to_scenario_action = QAction("Повернутись до сценарію", self)
        self.back_to_scenario_action.triggered.connect(self.return_to_scenario) # Метод MainWindow

    def _create_menu_bar(self):
        # Без змін
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("&Файл")
        file_menu.addAction(self.new_action);
        file_menu.addAction(self.import_action);
        file_menu.addAction(self.export_action)
        edit_menu = menu_bar.addMenu("&Правка")
        edit_menu.addAction(self.undo_action)
        edit_menu.addAction(self.redo_action)
        edit_menu.addSeparator()
        edit_menu.addAction(self.copy_action)
        edit_menu.addAction(self.paste_action)
        edit_menu.addSeparator()
        edit_menu.addAction(self.add_comment_action)
        edit_menu.addSeparator()
        edit_menu.addAction(self.back_to_scenario_action)

    def _create_toolbars(self): # Перейменовано
        log.debug("Creating toolbars...") # Діагностика
        # Видаляємо старі динамічні тулбари
        for toolbar in self.findChildren(QToolBar):
            # --- ЗМІНА: НЕ видаляємо sim_toolbar тут ---
            if toolbar.objectName() in ["scenario_toolbar", "macro_toolbar"]:
            # --- КІНЕЦЬ ЗМІНИ ---
                log.debug(f"Removing existing toolbar: {toolbar.objectName()}") # Діагностика
                self.removeToolBar(toolbar)
                toolbar.deleteLater()
        # Оновлюємо динамічні
        self._update_node_toolbars()
        log.debug("Toolbars created/updated.") # Діагностика

    def _update_node_toolbars(self):
        log.debug("Updating node toolbars...") # Діагностика
        # Видаляємо існуючі, якщо є
        if hasattr(self, 'scenario_toolbar'):
             log.debug("Removing existing scenario_toolbar") # Діагностика
             self.removeToolBar(self.scenario_toolbar)
             self.scenario_toolbar.deleteLater()
             del self.scenario_toolbar
        if hasattr(self, 'macro_toolbar'):
             log.debug("Removing existing macro_toolbar") # Діагностика
             self.removeToolBar(self.macro_toolbar)
             self.macro_toolbar.deleteLater()
             del self.macro_toolbar

        if self.current_edit_mode == EDIT_MODE_SCENARIO:
            log.debug("Creating scenario toolbar") # Діагностика
            self._create_scenario_toolbar()
            self.back_to_scenario_action.setEnabled(False)
        elif self.current_edit_mode == EDIT_MODE_MACRO:
            log.debug("Creating macro toolbar") # Діагностика
            self._create_macro_toolbar()
            self.back_to_scenario_action.setEnabled(True)

        # Оновлюємо видимість панелі симуляції
        # --- ЗМІНА: Перевіряємо наявність sim_toolbar більш надійно ---
        sim_toolbar = self.findChild(QToolBar, "simulation_toolbar")
        if sim_toolbar:
        # --- КІНЕЦЬ ЗМІНИ ---
            log.debug(f"Setting sim_toolbar visibility: {self.current_edit_mode == EDIT_MODE_SCENARIO}") # Діагностика
            sim_toolbar.setVisible(self.current_edit_mode == EDIT_MODE_SCENARIO)
        else:
            # Це попередження тепер не повинно з'являтися через зміну порядку в __init__
            log.warning("_update_node_toolbars: sim_toolbar not found.") # Діагностика

    def _create_scenario_toolbar(self):
        self.scenario_toolbar = QToolBar("Основна панель")
        self.scenario_toolbar.setObjectName("scenario_toolbar")
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.scenario_toolbar)
        # --- Логіка без змін ---
        INTERNAL_NODE_NAMES = ["Вхід Макроса", "Вихід Макроса"]
        for node_type in sorted(NODE_REGISTRY.keys()):
            if node_type in INTERNAL_NODE_NAMES: continue
            node_class = NODE_REGISTRY[node_type]
            icon = getattr(node_class, 'ICON', '●')
            action = QAction(icon, self)
            action.setToolTip(f"Додати вузол '{node_type}'")
            action.triggered.connect(lambda checked=False, nt=node_type: self._on_toolbar_action_triggered(nt))
            self.scenario_toolbar.addAction(action)

    def _create_macro_toolbar(self):
        self.macro_toolbar = QToolBar("Панель редагування макросу")
        self.macro_toolbar.setObjectName("macro_toolbar")
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.macro_toolbar)
        # --- Логіка без змін ---
        back_action = QAction("⬅️ Повернутись", self)
        back_action.triggered.connect(self.return_to_scenario)
        self.macro_toolbar.addAction(back_action)
        self.macro_toolbar.addSeparator()
        for node_type in ["Вхід Макроса", "Вихід Макроса"]:
            node_class = NODE_REGISTRY[node_type]
            icon = getattr(node_class, 'ICON', '●')
            action = QAction(icon, self)
            action.setToolTip(f"Додати вузол '{node_type}'")
            action.triggered.connect(lambda checked=False, nt=node_type: self._on_toolbar_action_triggered(nt))
            self.macro_toolbar.addAction(action)

    def _create_simulation_toolbar(self):
        log.debug("Creating simulation toolbar...") # Діагностика
        # --- ЗМІНА: Додано перевірку, щоб не створювати дублікат ---
        if hasattr(self, 'sim_toolbar'):
            log.warning("Simulation toolbar already exists. Skipping creation.")
            return
        # --- КІНЕЦЬ ЗМІНИ ---
        self.sim_toolbar = QToolBar("Панель симуляції")
        self.sim_toolbar.setObjectName("simulation_toolbar")
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.sim_toolbar)
        # --- Логіка без змін ---
        self.start_sim_action = QAction(QIcon.fromTheme("media-playback-start"), "Старт", self)
        self.start_sim_action.triggered.connect(self.start_simulation)
        self.sim_toolbar.addAction(self.start_sim_action)
        self.step_sim_action = QAction(QIcon.fromTheme("media-seek-forward"), "Крок", self)
        self.step_sim_action.triggered.connect(self.step_simulation)
        self.sim_toolbar.addAction(self.step_sim_action)
        self.stop_sim_action = QAction(QIcon.fromTheme("media-playback-stop"), "Стоп", self)
        self.stop_sim_action.triggered.connect(self.stop_simulation)
        self.sim_toolbar.addAction(self.stop_sim_action)
        self.sim_toolbar.addSeparator()
        self.sim_trigger_zone_combo = QComboBox(self)
        self.sim_trigger_zone_combo.setToolTip("Виберіть зону для запуску симуляції")
        self.sim_trigger_zone_combo.setMinimumWidth(250)
        self.sim_trigger_zone_combo.currentIndexChanged.connect(self.update_simulation_controls)
        self.sim_toolbar.addWidget(QLabel("  Зона тригера: "))
        self.sim_toolbar.addWidget(self.sim_trigger_zone_combo)
        # Початково може бути невидимий, якщо стартуємо в режимі макросу (хоча зараз стартуємо в сценарії)
        self.sim_toolbar.setVisible(self.current_edit_mode == EDIT_MODE_SCENARIO)
        log.debug("Simulation toolbar created.") # Діагностика

    def _on_toolbar_action_triggered(self, node_type):
        log.debug(f"Toolbar action triggered: Add node '{node_type}'") # Діагностика
        self.add_node(node_type, self.view.mapToScene(self.view.viewport().rect().center()))

    # --- Panels ---
    def _create_panels(self):
        log.debug("Creating panels...") # Діагностика
        # --- Панель Проекта ---
        project_dock = QDockWidget("Проект", self)
        self.project_tabs = QTabWidget()
        # Вкладка Сценарії
        scenarios_widget = QWidget()
        scenarios_layout = QVBoxLayout(scenarios_widget)
        self.scenarios_list = QListWidget() # Залишаємо віджет списку тут
        scenarios_layout.addWidget(self.scenarios_list)
        scenarios_btn_layout = QHBoxLayout()
        add_scenario_btn = QPushButton("Додати")
        remove_scenario_btn = QPushButton("Видалити")
        scenarios_btn_layout.addWidget(add_scenario_btn)
        scenarios_btn_layout.addWidget(remove_scenario_btn)
        scenarios_layout.addLayout(scenarios_btn_layout)
        # Вкладка Макроси
        macros_widget = QWidget()
        macros_layout = QVBoxLayout(macros_widget)
        self.macros_list = QListWidget() # Залишаємо віджет списку тут
        macros_layout.addWidget(self.macros_list)
        macros_btn_layout = QHBoxLayout()
        remove_macro_btn = QPushButton("Видалити")
        rename_macro_btn = QPushButton("Перейменувати")
        macros_btn_layout.addWidget(rename_macro_btn)
        macros_btn_layout.addWidget(remove_macro_btn)
        macros_layout.addLayout(macros_btn_layout)
        # Додавання вкладок
        self.project_tabs.addTab(scenarios_widget, "Сценарії")
        self.project_tabs.addTab(macros_widget, "Макроси")
        project_dock.setWidget(self.project_tabs)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, project_dock)
        log.debug("Project panel created.") # Діагностика

        # --- Панель Елементів ---
        nodes_dock = QDockWidget("Елементи сценарію", self)
        self.nodes_list = QListWidget() # Залишаємо віджет списку тут
        self._update_nodes_list() # Початкове заповнення
        nodes_dock.setWidget(self.nodes_list)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, nodes_dock)
        log.debug("Elements panel created.") # Діагностика

        # --- Панель Конфігурації ---
        config_dock = QDockWidget("Конфігурація системи", self)
        config_tabs = QTabWidget()
        # Пристрої
        devices_widget = QWidget()
        devices_layout = QVBoxLayout(devices_widget)
        self.devices_table = QTableWidget(0, 3) # Залишаємо таблицю
        self.devices_table.setHorizontalHeaderLabels(["ID", "Назва пристрою", "Тип"])
        self.devices_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.devices_table.hideColumn(0)
        devices_layout.addWidget(self.devices_table)
        add_device_layout = QHBoxLayout()
        self.device_type_combo = QComboBox() # Залишаємо комбобокс
        self.device_type_combo.addItems(DEVICE_SPECS.keys())
        add_device_btn = QPushButton("Додати пристрій")
        remove_device_btn = QPushButton("Видалити пристрій")
        add_device_layout.addWidget(self.device_type_combo)
        add_device_layout.addWidget(add_device_btn)
        add_device_layout.addWidget(remove_device_btn)
        devices_layout.addLayout(add_device_layout)
        config_tabs.addTab(devices_widget, "Пристрої")
        # Зони
        zones_widget = QWidget()
        zones_layout = QVBoxLayout(zones_widget)
        self.zones_table = QTableWidget(0, 3) # Залишаємо таблицю
        self.zones_table.setHorizontalHeaderLabels(["ID", "Пристрій", "Назва зони"])
        self.zones_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.zones_table.hideColumn(0)
        zones_layout.addWidget(self.zones_table)
        config_tabs.addTab(zones_widget, "Зони")
        # Виходи
        outputs_widget = QWidget()
        outputs_layout = QVBoxLayout(outputs_widget)
        self.outputs_table = QTableWidget(0, 3) # Залишаємо таблицю
        self.outputs_table.setHorizontalHeaderLabels(["ID", "Пристрій", "Назва виходу"])
        self.outputs_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.outputs_table.hideColumn(0)
        outputs_layout.addWidget(self.outputs_table)
        config_tabs.addTab(outputs_widget, "Виходи")
        # Користувачі
        users_widget = QWidget()
        users_layout = QVBoxLayout(users_widget)
        self.users_table = QTableWidget(0, 3) # Залишаємо таблицю
        self.users_table.setHorizontalHeaderLabels(["ID", "Ім'я користувача", "Телефон"])
        self.users_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.users_table.hideColumn(0)
        users_layout.addWidget(self.users_table)
        users_btn_layout = QHBoxLayout()
        add_user_btn = QPushButton("Додати")
        remove_user_btn = QPushButton("Видалити")
        users_btn_layout.addWidget(add_user_btn)
        users_btn_layout.addWidget(remove_user_btn)
        users_layout.addLayout(users_btn_layout)
        config_tabs.addTab(users_widget, "Користувачі")
        # Додавання панелі
        config_dock.setWidget(config_tabs)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, config_dock)
        log.debug("Configuration panel created.") # Діагностика

        # --- Панель Властивостей ---
        props_dock = QDockWidget("Властивості", self)
        # --- ЗМІНА: Задаємо мінімальну ширину, щоб панель не стрибала ---
        props_dock.setMinimumWidth(320)
        log.debug("Setting minimum width for properties panel.") # Діагностика
        # --- КІНЕЦЬ ЗМІНИ ---

        self.props_widget = QWidget() # Головний віджет панелі властивостей
        self.main_props_layout = QVBoxLayout(self.props_widget) # Layout для цього віджету
        # --- ЗМІНА: Додаємо відступи та інтервал ---
        self.main_props_layout.setContentsMargins(5, 5, 5, 5)
        self.main_props_layout.setSpacing(10)
        log.debug("Set margins and spacing for main properties layout.") # Діагностика
        # --- КІНЕЦЬ ЗМІНИ ---
        props_dock.setWidget(self.props_widget)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, props_dock)
        self.setup_properties_panel() # Створення елементів UI властивостей залишається тут
        self._update_properties_panel_ui() # Початкове налаштування видимості
        self.props_widget.setEnabled(False) # Початково вимкнена
        log.debug("Properties panel created.") # Діагностика

        # --- Підключення сигналів ---
        log.debug("Connecting panel signals...") # Діагностика
        # Проект
        add_scenario_btn.clicked.connect(self.add_scenario) # Виклик методу MainWindow
        remove_scenario_btn.clicked.connect(self.remove_scenario) # Виклик методу MainWindow
        remove_macro_btn.clicked.connect(self.remove_macro) # Виклик методу MainWindow
        rename_macro_btn.clicked.connect(self.rename_macro) # Виклик методу MainWindow
        self.scenarios_list.currentItemChanged.connect(self.on_active_scenario_changed)
        self.scenarios_list.itemDoubleClicked.connect(self.on_scenario_item_double_clicked)
        self.scenarios_list.itemChanged.connect(self.on_scenario_renamed) # Залишаємо тут через _old_scenario_name
        self.macros_list.itemDoubleClicked.connect(self.on_macro_item_double_clicked)
        self.project_tabs.currentChanged.connect(self.on_project_tab_changed)
        # Елементи
        self.nodes_list.itemClicked.connect(self.on_node_list_clicked)
        # Конфігурація
        add_device_btn.clicked.connect(self.add_device) # Виклик методу MainWindow
        remove_device_btn.clicked.connect(self.remove_device) # Виклик методу MainWindow
        add_user_btn.clicked.connect(lambda: self.add_config_item('users')) # Виклик методу MainWindow
        remove_user_btn.clicked.connect(lambda: self.remove_config_item('users')) # Виклик методу MainWindow
        # Сигнали від таблиць конфігурації (залишаються тут, бо оновлюють дані менеджера)
        self.devices_table.itemChanged.connect(self.on_config_table_changed)
        self.zones_table.itemChanged.connect(self.on_config_table_changed)
        self.outputs_table.itemChanged.connect(self.on_config_table_changed)
        self.users_table.itemChanged.connect(self.on_config_table_changed)
        # Властивості (сигнали від полів вводу підключаються в setup_properties_panel)
        log.debug("Panel signals connected.") # Діагностика

        # --- ВИДАЛЕНО ЗАЙВИЙ ДІАГНОСТИЧНИЙ БЛОК ТА ВИПРАВЛЕННЯ ДЛЯ props_scroll_area ---
        # log.debug(f"Checking self.props_scroll_area existence before line 339:") # Діагностика
        # if hasattr(self, 'props_scroll_area'):
        #     log.debug(f"  self.props_scroll_area found: {self.props_scroll_area}") # Діагностика
        #     self.props_scroll_area.setFrameShape(QFrame.Shape.NoFrame) # ПРАВИЛЬНО
        #     log.debug("  Successfully applied setFrameShape(QFrame.Shape.NoFrame) to self.props_scroll_area") # Діагностика
        # else:
        #     log.warning("  self.props_scroll_area NOT FOUND at line ~339!") # Діагностика
        # --- КІНЕЦЬ ВИДАЛЕННЯ ---

        log.debug("Panels creation finished.") # Діагностика

    def on_project_tab_changed(self, index):
        # Логіка без змін
        log.debug(f"Project tab changed to index: {index}") # Діагностика
        if index == 0 and self.current_edit_mode == EDIT_MODE_MACRO:
            self.return_to_scenario()

    def _update_nodes_list(self):
        log.debug(f"Updating nodes list for mode: {self.current_edit_mode}") # Діагностика
        # Логіка без змін
        self.nodes_list.clear()
        if self.current_edit_mode == EDIT_MODE_SCENARIO:
            items = sorted([name for name in NODE_REGISTRY.keys() if name not in ["Вхід Макроса", "Вихід Макроса"]])
        elif self.current_edit_mode == EDIT_MODE_MACRO:
            items = sorted(["Вхід Макроса", "Вихід Макроса", "Затримка", "Умова: Стан зони", "Повтор"])
        self.nodes_list.addItems(items)
        log.debug(f"Nodes list updated with {len(items)} items.") # Діагностика

    # --- Properties Panel Setup and Update (залишаються тут) ---
    def setup_properties_panel(self):
        log.debug("Setting up properties panel...") # Діагностика
        # Цей метод створює віджети (QLineEdit, QComboBox і т.д.) для панелі властивостей
        # Логіка створення віджетів не змінилася
        # Видаляємо старі віджети
        log.debug("Clearing existing widgets from main properties layout...") # Діагностика
        while self.main_props_layout.count():
             item = self.main_props_layout.takeAt(0)
             widget = item.widget()
             if widget: widget.deleteLater()
             else: # Layouts or Spacers
                 layout = item.layout()
                 if layout:
                      while layout.count():
                           child = layout.takeAt(0)
                           if child.widget(): child.widget().deleteLater()
                      layout.deleteLater()
        log.debug("Existing widgets cleared.") # Діагностика

        # Базові властивості
        self.base_props_widget = QWidget()
        base_props_layout = QFormLayout(self.base_props_widget)
        self.prop_name = QLineEdit()
        self.prop_description = QTextEdit()
        self.prop_description.setFixedHeight(80)
        base_props_layout.addRow("Назва:", self.prop_name)
        base_props_layout.addRow("Опис:", self.prop_description)
        self.main_props_layout.addWidget(self.base_props_widget)
        self.prop_name.textChanged.connect(self._schedule_properties_apply) # Підключення сигналу
        self.prop_description.textChanged.connect(self._schedule_properties_apply) # Підключення сигналу
        log.debug("Base properties widget created.") # Діагностика

        # --- ЗМІНА: Створюємо QStackedWidget для всіх динамічних властивостей ---
        self.props_stacked_widget = QStackedWidget(self.props_widget)
        self.main_props_layout.addWidget(self.props_stacked_widget)
        log.debug("props_stacked_widget created.") # Діагностика

        # Створюємо віджет-заглушку для стану "нічого не вибрано"
        self.props_placeholder_widget = QWidget()
        placeholder_layout = QVBoxLayout(self.props_placeholder_widget)
        placeholder_layout.addStretch(1)
        placeholder_label = QLabel("Виберіть вузол для\nредагування властивостей.")
        placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder_label.setStyleSheet("color: #888;")
        placeholder_layout.addWidget(placeholder_label)
        placeholder_layout.addStretch(1)
        self.props_stacked_widget.addWidget(self.props_placeholder_widget)
        log.debug("Placeholder widget ('nothing selected') created.") # Діагностика

        # Створюємо віджет-заглушку для вузлів, які мають лише базові властивості
        self.props_node_specific_placeholder = QWidget()
        self.props_stacked_widget.addWidget(self.props_node_specific_placeholder)
        log.debug("Placeholder widget ('base props only') created.") # Діагностика
        # --- КІНЕЦЬ ЗМІНИ ---

        # Властивості Тригера
        self.trigger_props_widget = QWidget()
        trigger_layout = QFormLayout(self.trigger_props_widget)
        self.trigger_type_combo = QComboBox()
        self.trigger_type_combo.addItems(["Пожежа", "Тривога", "Несправність 220В", "Зняття з охорони"])
        trigger_layout.addRow("Спосіб запуску:", self.trigger_type_combo)
        self.zones_container = QWidget()
        self.zones_layout = QGridLayout(self.zones_container)
        self.zones_layout.setContentsMargins(0, 0, 0, 0)
        # --- ЗМІНА: Зберігаємо scroll_area як self.trigger_zones_scroll_area ---
        self.trigger_zones_scroll_area = QScrollArea()
        self.trigger_zones_scroll_area.setWidgetResizable(True)
        self.trigger_zones_scroll_area.setWidget(self.zones_container)
        self.trigger_zones_scroll_area.setMinimumHeight(100)
        # --- ВИПРАВЛЕННЯ: Встановлюємо NoFrame тут ---
        self.trigger_zones_scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        log.debug("Set NoFrame for trigger_zones_scroll_area") # Діагностика
        # --- КІНЕЦЬ ВИПРАВЛЕННЯ ---
        trigger_layout.addRow("Зони:", self.trigger_zones_scroll_area)
        # --- КІНЕЦЬ ЗМІНИ ---
        # --- ЗМІНА: Додаємо віджет до QStackedWidget ---
        self.props_stacked_widget.addWidget(self.trigger_props_widget)
        # --- КІНЕЦЬ ЗМІНИ ---
        self.trigger_type_combo.currentTextChanged.connect(self._schedule_properties_apply) # Підключення сигналу
        log.debug("Trigger properties widget created.") # Діагностика

        # Властивості Виходу
        self.output_props_widget = QWidget()
        output_layout = QFormLayout(self.output_props_widget)
        self.output_select_combo = QComboBox()
        output_layout.addRow("Вихід:", self.output_select_combo)
        # --- ЗМІНА: Додаємо віджет до QStackedWidget ---
        self.props_stacked_widget.addWidget(self.output_props_widget)
        # --- КІНЕЦЬ ЗМІНИ ---
        self.output_select_combo.currentIndexChanged.connect(self._schedule_properties_apply) # Підключення сигналу
        log.debug("Output properties widget created.") # Діагностика

        # Властивості Затримки
        self.delay_props_widget = QWidget()
        delay_layout = QFormLayout(self.delay_props_widget)
        self.delay_spinbox = QSpinBox()
        self.delay_spinbox.setRange(0, 3600); self.delay_spinbox.setSuffix(" сек.")
        delay_layout.addRow("Час затримки:", self.delay_spinbox)
        # --- ЗМІНА: Додаємо віджет до QStackedWidget ---
        self.props_stacked_widget.addWidget(self.delay_props_widget)
        # --- КІНЕЦЬ ЗМІНИ ---
        self.delay_spinbox.valueChanged.connect(self._schedule_properties_apply) # Підключення сигналу
        log.debug("Delay properties widget created.") # Діагностика

        # Властивості SMS
        self.sms_props_widget = QWidget()
        sms_layout = QFormLayout(self.sms_props_widget)
        self.sms_user_combo = QComboBox()
        self.sms_message_text = QLineEdit()
        sms_layout.addRow("Користувач:", self.sms_user_combo)
        sms_layout.addRow("Повідомлення:", self.sms_message_text)
        # --- ЗМІНА: Додаємо віджет до QStackedWidget ---
        self.props_stacked_widget.addWidget(self.sms_props_widget)
        # --- КІНЕЦЬ ЗМІНИ ---
        self.sms_user_combo.currentIndexChanged.connect(self._schedule_properties_apply) # Підключення сигналу
        self.sms_message_text.textChanged.connect(self._schedule_properties_apply) # Підключення сигналу
        log.debug("SMS properties widget created.") # Діагностика

        # Властивості Умови
        self.condition_props_widget = QWidget()
        condition_layout = QFormLayout(self.condition_props_widget)
        self.condition_zone_combo = QComboBox()
        self.condition_state_combo = QComboBox()
        self.condition_state_combo.addItems(["Під охороною", "Знята з охорони", "Тривога"])
        condition_layout.addRow("Зона:", self.condition_zone_combo)
        condition_layout.addRow("Перевірити стан:", self.condition_state_combo)
        # --- ЗМІНА: Додаємо віджет до QStackedWidget ---
        self.props_stacked_widget.addWidget(self.condition_props_widget)
        # --- КІНЕЦЬ ЗМІНИ ---
        self.condition_zone_combo.currentIndexChanged.connect(self._schedule_properties_apply) # Підключення сигналу
        self.condition_state_combo.currentIndexChanged.connect(self._schedule_properties_apply) # Підключення сигналу
        log.debug("Condition properties widget created.") # Діагностика

        # Властивості Повтору
        self.repeat_props_widget = QWidget()
        repeat_layout = QFormLayout(self.repeat_props_widget)
        self.repeat_count_spinbox = QSpinBox()
        self.repeat_count_spinbox.setRange(-1, 100); self.repeat_count_spinbox.setSpecialValueText("Безкінечно")
        repeat_layout.addRow("Кількість повторів:", self.repeat_count_spinbox)
        # --- ЗМІНА: Додаємо віджет до QStackedWidget ---
        self.props_stacked_widget.addWidget(self.repeat_props_widget)
        # --- КІНЕЦЬ ЗМІНИ ---
        self.repeat_count_spinbox.valueChanged.connect(self._schedule_properties_apply) # Підключення сигналу
        log.debug("Repeat properties widget created.") # Діагностика

        # Властивості Макросу
        self.macro_props_widget = QWidget()
        macro_layout = QFormLayout(self.macro_props_widget)
        self.macro_definition_combo = QComboBox()
        macro_layout.addRow("Визначення:", self.macro_definition_combo)
        # --- ЗМІНА: Додаємо віджет до QStackedWidget ---
        self.props_stacked_widget.addWidget(self.macro_props_widget)
        # --- КІНЕЦЬ ЗМІНИ ---
        self.macro_definition_combo.currentIndexChanged.connect(self._schedule_properties_apply) # Підключення сигналу
        log.debug("MacroNode properties widget created.") # Діагностика

        # --- ЗМІНА: Замінюємо QSpacerItem на addStretch ---
        self.main_props_layout.addStretch(1) # Додаємо розтягування в кінець
        log.debug("Added stretch to main properties layout.") # Діагностика
        # --- КІНЕЦЬ ЗМІНИ ---
        log.debug("Properties panel setup finished.") # Діагностика

    def _schedule_properties_apply(self):
        log.debug("Scheduling properties apply timer...") # Діагностика
        # Логіка без змін
        if self.props_widget.isEnabled():
            self.props_apply_timer.start()

    def on_selection_changed(self):
        log.debug("Selection changed.") # Діагностика
        # Логіка без змін, вона працює з self.current_selected_node та self.props_widget
        if self.simulator.is_running:
            log.debug("  Ignoring selection change during simulation.") # Діагностика
            return
        self.props_apply_timer.stop()
        log.debug("  Stopped properties apply timer.") # Діагностика
        selected_items = self.scene.selectedItems()
        newly_selected_node = None
        if len(selected_items) == 1 and isinstance(selected_items[0], BaseNode):
            newly_selected_node = selected_items[0]

        # Не застосовуємо зміни властивостей, якщо вибір змінився на інший вузол або зник
        if self.current_selected_node and self.current_selected_node != newly_selected_node:
             pass # Можливо, тут варто було б спробувати зберегти, але таймер це робить
             log.debug(f"  Selection changed from {self.current_selected_node.id} to {newly_selected_node.id if newly_selected_node else 'None'}.") # Діагностика

        self.current_selected_node = newly_selected_node
        log.debug(f"  Current selected node is now: {self.current_selected_node.id if self.current_selected_node else 'None'}") # Діагностика
        self.props_widget.setEnabled(self.current_selected_node is not None)
        self._update_properties_panel_ui()

    def _update_properties_panel_ui(self):
        log.debug("Updating properties panel UI...") # Діагностика
        # Цей метод оновлює значення та видимість віджетів у панелі властивостей
        # Він використовує self.current_selected_node та віджети, створені в setup_properties_panel
        # --- ЗАМІНА: Отримуємо дані конфігурації з менеджера ---
        config_data = self.project_manager.get_config_data()
        all_zones, all_outputs = self.project_manager.get_all_zones_and_outputs()
        macros_data = self.project_manager.get_macros_data()
        log.debug(f"  Got config data: {len(all_zones)} zones, {len(all_outputs)} outputs, {len(macros_data)} macros.") # Діагностика
        # --- КІНЕЦЬ ЗАМІНИ ---

        node = self.current_selected_node if self.props_widget.isEnabled() else None
        is_node_selected = node is not None
        log.debug(f"  Node selected: {is_node_selected} ({node.id if node else 'N/A'})") # Діагностика

        # --- ЗМІНА: Логіка перемикання QStackedWidget ---

        # Показати базову панель, якщо щось вибрано
        self.base_props_widget.setVisible(is_node_selected)
        self.prop_name.setEnabled(is_node_selected)
        log.debug(f"  Base props widget visible: {is_node_selected}") # Діагностика

        widget_to_show = self.props_placeholder_widget # Заглушка "нічого не вибрано"

        if is_node_selected:
            log.debug("  Populating base properties...") # Діагностика
            # Заповнити базові поля
            self.prop_name.blockSignals(True); self.prop_description.blockSignals(True)
            self.prop_name.setText(node.node_name)
            self.prop_description.setPlainText(node.description)
            self.prop_name.blockSignals(False); self.prop_description.blockSignals(False)
            log.debug(f"    Name: '{node.node_name}', Desc: '{node.description[:20]}...'") # Діагностика

            # Показати та заповнити специфічну панель
            props = dict(node.properties) # Поточні властивості вузла
            log.debug(f"  Node properties: {props}") # Діагностика

            # За замовчуванням показуємо порожню заглушку для вузлів без спец. властивостей
            widget_to_show = self.props_node_specific_placeholder
            log.debug("  Default specific widget: props_node_specific_placeholder") # Діагностика

            if isinstance(node, TriggerNode):
                log.debug("  Setting up TriggerNode properties...") # Діагностика
                widget_to_show = self.trigger_props_widget # Вказуємо, який віджет показати
                self.trigger_type_combo.blockSignals(True)
                self.trigger_type_combo.setCurrentText(props.get('trigger_type', 'Пожежа'))
                self.trigger_type_combo.blockSignals(False)
                # Очистка та заповнення чекбоксів зон
                log.debug("    Clearing zone checkboxes...") # Діагностика
                while self.zones_layout.count():
                    child = self.zones_layout.takeAt(0)
                    if child.widget(): child.widget().deleteLater()
                selected_zones = props.get('zones', [])
                log.debug(f"    Selected zones: {selected_zones}") # Діагностика
                log.debug(f"    Populating {len(all_zones)} zone checkboxes...") # Діагностика
                for i, zone in enumerate(all_zones):
                    checkbox = QCheckBox(f"{zone['parent_name']}: {zone['name']}")
                    checkbox.setChecked(zone['id'] in selected_zones)
                    checkbox.toggled.connect(self._schedule_properties_apply) # Сигнал підключено
                    checkbox.setProperty("zone_id", zone['id'])
                    self.zones_layout.addWidget(checkbox, i // 2, i % 2)
                log.debug("    Zone checkboxes populated.") # Діагностика

            elif isinstance(node, (ActivateOutputNode, DeactivateOutputNode)):
                log.debug("  Setting up OutputNode properties...") # Діагностика
                widget_to_show = self.output_props_widget # Вказуємо, який віджет показати
                self.output_select_combo.blockSignals(True)
                self.output_select_combo.clear()
                self.output_select_combo.addItem("Не призначено", userData=None)
                for output in all_outputs:
                     self.output_select_combo.addItem(f"{output['parent_name']}: {output['name']}", userData=output['id'])
                selected_id = props.get('output_id')
                index = self.output_select_combo.findData(selected_id) if selected_id else 0
                log.debug(f"    Selected output ID: {selected_id}, Index: {index}") # Діагностика
                self.output_select_combo.setCurrentIndex(max(0, index))
                self.output_select_combo.blockSignals(False)

            elif isinstance(node, DelayNode):
                log.debug("  Setting up DelayNode properties...") # Діагностика
                widget_to_show = self.delay_props_widget # Вказуємо, який віджет показати
                self.delay_spinbox.blockSignals(True)
                val = int(props.get('seconds', 0))
                log.debug(f"    Setting delay value: {val}") # Діагностика
                self.delay_spinbox.setValue(val)
                self.delay_spinbox.blockSignals(False)

            elif isinstance(node, SendSMSNode):
                log.debug("  Setting up SendSMSNode properties...") # Діагностика
                widget_to_show = self.sms_props_widget # Вказуємо, який віджет показати
                self.sms_user_combo.blockSignals(True)
                self.sms_user_combo.clear()
                self.sms_user_combo.addItem("Не призначено", userData=None)
                # --- ВИКОРИСТАННЯ config_data ---
                for user in config_data.get('users', []):
                     self.sms_user_combo.addItem(user['name'], userData=user['id'])
                selected_id = props.get('user_id')
                index = self.sms_user_combo.findData(selected_id) if selected_id else 0
                log.debug(f"    Selected user ID: {selected_id}, Index: {index}") # Діагностика
                self.sms_user_combo.setCurrentIndex(max(0, index))
                self.sms_user_combo.blockSignals(False)
                self.sms_message_text.blockSignals(True)
                msg = props.get('message', '')
                log.debug(f"    Setting message: '{msg}'") # Діагностика
                self.sms_message_text.setText(msg)
                self.sms_message_text.blockSignals(False)

            elif isinstance(node, ConditionNodeZoneState):
                log.debug("  Setting up ConditionNodeZoneState properties...") # Діагностика
                widget_to_show = self.condition_props_widget # Вказуємо, який віджет показати
                self.condition_zone_combo.blockSignals(True)
                self.condition_zone_combo.clear()
                self.condition_zone_combo.addItem("Не призначено", userData=None)
                for zone in all_zones:
                     self.condition_zone_combo.addItem(f"{zone['parent_name']}: {zone['name']}", userData=zone['id'])
                selected_id = props.get('zone_id')
                index = self.condition_zone_combo.findData(selected_id) if selected_id else 0
                log.debug(f"    Selected zone ID: {selected_id}, Index: {index}") # Діагностика
                self.condition_zone_combo.setCurrentIndex(max(0, index))
                self.condition_zone_combo.blockSignals(False)
                self.condition_state_combo.blockSignals(True)
                state = props.get('state', 'Під охороною')
                log.debug(f"    Setting state: '{state}'") # Діагностика
                self.condition_state_combo.setCurrentText(state)
                self.condition_state_combo.blockSignals(False)

            elif isinstance(node, RepeatNode):
                log.debug("  Setting up RepeatNode properties...") # Діагностика
                widget_to_show = self.repeat_props_widget # Вказуємо, який віджет показати
                self.repeat_count_spinbox.blockSignals(True)
                count = int(props.get('count', 3))
                log.debug(f"    Setting count: {count}") # Діагностика
                self.repeat_count_spinbox.setValue(count)
                self.repeat_count_spinbox.blockSignals(False)

            elif isinstance(node, MacroNode):
                log.debug("  Setting up MacroNode properties...") # Діагностика
                widget_to_show = self.macro_props_widget # Вказуємо, який віджет показати
                self.macro_definition_combo.blockSignals(True)
                self.macro_definition_combo.clear()
                self.macro_definition_combo.addItem("Не призначено", userData=None)
                # --- ВИКОРИСТАННЯ macros_data ---
                log.debug(f"    Populating {len(macros_data)} macro definitions...") # Діагностика
                for macro_id, macro_data_item in macros_data.items(): # Змінено змінну
                    self.macro_definition_combo.addItem(macro_data_item.get('name', macro_id), userData=macro_id)
                selected_id = node.macro_id # Властивість MacroNode
                index = self.macro_definition_combo.findData(selected_id) if selected_id else 0
                log.debug(f"    Selected macro ID: {selected_id}, Index: {index}") # Діагностика
                self.macro_definition_combo.setCurrentIndex(max(0, index))
                self.macro_definition_combo.blockSignals(False)

            # Інші вузли (Sequence, MacroInput, MacroOutput)
            # будуть використовувати self.props_node_specific_placeholder,
            # який ми встановили за замовчуванням.
            else:
                 log.debug(f"  Node type {type(node).__name__} uses placeholder for specific properties.") # Діагностика

        else: # Якщо нічого не вибрано
            log.debug("  No node selected, clearing base properties.") # Діагностика
            self.prop_name.clear()
            self.prop_description.clear()
            widget_to_show = self.props_placeholder_widget # Повертаємо заглушку "нічого не вибрано"

        # Встановлюємо поточний віджет у QStackedWidget
        log.debug(f"  Setting current widget in stacked widget: {widget_to_show.metaObject().className()}") # Діагностика
        self.props_stacked_widget.setCurrentWidget(widget_to_show)
        log.debug("Properties panel UI update finished.") # Діагностика
        # --- КІНЕЦЬ ЗМІНИ ---

    # --- Збереження властивостей (залишається тут) ---
    def on_apply_button_clicked(self):
        log.debug("Apply button (timer) triggered.") # Діагностика
        # Цей метод читає значення з віджетів панелі властивостей
        # і створює команду ChangePropertiesCommand
        if not self.current_selected_node:
            log.debug("  No node selected, apply aborted.") # Діагностика
            return
        node = self.current_selected_node
        log.debug(f"Applying properties for node {node.id} ({node.node_name})") # Діагностика

        # Зберігаємо старі дані для undo
        old_name = node.node_name
        old_desc = node.description
        old_props = list(node.properties) # Копія списку
        old_macro_id = getattr(node, 'macro_id', None)
        old_data = {'name': old_name, 'desc': old_desc, 'props': old_props, 'macro_id': old_macro_id}
        log.debug(f"  Old data: {old_data}") # Діагностика

        # Зчитуємо нові дані з UI
        new_name = self.prop_name.text()
        new_desc = self.prop_description.toPlainText()
        new_props_list = old_props # За замовчуванням
        new_macro_id = old_macro_id # За замовчуванням

        try: # Додаємо try-except для надійності зчитування UI
             if isinstance(node, TriggerNode):
                 trigger_type = self.trigger_type_combo.currentText()
                 selected_zones = []
                 for i in range(self.zones_layout.count()):
                     widget = self.zones_layout.itemAt(i).widget()
                     if isinstance(widget, QCheckBox) and widget.isChecked():
                         selected_zones.append(widget.property("zone_id"))
                 new_props_list = [('trigger_type', trigger_type), ('zones', selected_zones)]
             elif isinstance(node, (ActivateOutputNode, DeactivateOutputNode)):
                 new_props_list = [('output_id', self.output_select_combo.currentData() or '')]
             elif isinstance(node, DelayNode):
                 new_props_list = [('seconds', self.delay_spinbox.value())]
             elif isinstance(node, SendSMSNode):
                 new_props_list = [('user_id', self.sms_user_combo.currentData() or ''),
                                   ('message', self.sms_message_text.text())]
             elif isinstance(node, ConditionNodeZoneState):
                 new_props_list = [('zone_id', self.condition_zone_combo.currentData() or ''),
                                   ('state', self.condition_state_combo.currentText())]
             elif isinstance(node, RepeatNode):
                 new_props_list = [('count', self.repeat_count_spinbox.value())]
             elif isinstance(node, MacroNode):
                 new_macro_id = self.macro_definition_combo.currentData() or None
             elif isinstance(node, (MacroInputNode, MacroOutputNode)):
                 # Тільки ім'я та опис змінюються через базову панель
                 new_name = self.prop_name.text() # Ім'я вже отримано
                 new_props_list = [] # У них немає інших властивостей

             new_data = {'name': new_name, 'desc': new_desc, 'props': new_props_list, 'macro_id': new_macro_id}
             log.debug(f"  New data read from UI: {new_data}") # Діагностика

             # Створюємо команду, тільки якщо дані змінились
             if old_data != new_data:
                 log.info(f"  Properties changed for node {node.id}. Pushing ChangePropertiesCommand.") # Діагностика
                 # --- Оновлення імені у визначенні макросу ---
                 if self.current_edit_mode == EDIT_MODE_MACRO and self.active_macro_id \
                    and isinstance(node, (MacroInputNode, MacroOutputNode)):
                     io_type = 'input' if isinstance(node, MacroInputNode) else 'output'
                     log.debug(f"  Updating macro IO name in definition (MacroID: {self.active_macro_id}, NodeID: {node.id}, Type: {io_type}, NewName: '{new_name}')") # Діагностика
                     # --- ВИКОРИСТАННЯ project_manager ---
                     io_name_changed = self.project_manager.update_macro_io_name(self.active_macro_id, node.id, io_type, new_name)
                     # Оновлюємо сокети MacroNode в сценаріях після зміни імені IO
                     if io_name_changed:
                          log.debug("  IO name changed, updating MacroNodes in scenarios...") # Діагностика
                          self.update_macro_nodes_in_scenarios(self.active_macro_id)
                     else:
                          log.debug("  IO name did not actually change in definition.") # Діагностика
                     # --- КІНЕЦЬ ---

                 # --- Імпорт команди ---
                 from commands import ChangePropertiesCommand
                 command = ChangePropertiesCommand(node, old_data, new_data)
                 self.undo_stack.push(command)
             else:
                 log.debug("  Properties not changed, command not pushed.") # Діагностика

        except Exception as e:
             log.error(f"Error reading properties from UI for node {node.id}: {e}", exc_info=True)
             QMessageBox.warning(self, "Помилка", f"Не вдалося зчитати властивості: {e}")


    # --- Configuration Panel Actions ---
    def update_config_ui(self):
        """Оновлює таблиці конфігурації даними з ProjectManager."""
        log.debug("Updating configuration UI panels...") # Діагностика
        # --- ВИКОРИСТАННЯ project_manager ---
        config = self.project_manager.get_config_data()
        all_zones, all_outputs = self.project_manager.get_all_zones_and_outputs()
        # --- КІНЕЦЬ ---
        log.debug(f"  Got config data: {len(config.get('devices',[]))} devices, {len(all_zones)} zones, {len(all_outputs)} outputs, {len(config.get('users',[]))} users.") # Діагностика

        # Блокуємо сигнали таблиць
        self.devices_table.blockSignals(True); self.zones_table.blockSignals(True)
        self.outputs_table.blockSignals(True); self.users_table.blockSignals(True)
        log.debug("  Config tables signals blocked.") # Діагностика

        # Заповнення таблиці пристроїв
        self.devices_table.setRowCount(0)
        for device in config.get('devices', []):
            row = self.devices_table.rowCount()
            self.devices_table.insertRow(row)
            self.devices_table.setItem(row, 0, QTableWidgetItem(device['id']))
            self.devices_table.setItem(row, 1, QTableWidgetItem(device['name']))
            self.devices_table.setItem(row, 2, QTableWidgetItem(device['type']))

        # Заповнення таблиці зон (використовуємо згенерований список)
        self.zones_table.setRowCount(0)
        for zone in all_zones:
            row = self.zones_table.rowCount()
            self.zones_table.insertRow(row)
            self.zones_table.setItem(row, 0, QTableWidgetItem(zone['id']))
            self.zones_table.setItem(row, 1, QTableWidgetItem(zone.get('parent_name', ''))) # Використовуємо parent_name
            self.zones_table.setItem(row, 2, QTableWidgetItem(zone['name']))

        # Заповнення таблиці виходів (використовуємо згенерований список)
        self.outputs_table.setRowCount(0)
        for output in all_outputs:
            row = self.outputs_table.rowCount()
            self.outputs_table.insertRow(row)
            self.outputs_table.setItem(row, 0, QTableWidgetItem(output['id']))
            self.outputs_table.setItem(row, 1, QTableWidgetItem(output.get('parent_name', ''))) # Використовуємо parent_name
            self.outputs_table.setItem(row, 2, QTableWidgetItem(output['name']))

        # Заповнення таблиці користувачів
        self.users_table.setRowCount(0)
        for item in config.get('users', []):
            row = self.users_table.rowCount()
            self.users_table.insertRow(row)
            self.users_table.setItem(row, 0, QTableWidgetItem(item['id']))
            self.users_table.setItem(row, 1, QTableWidgetItem(item['name']))
            self.users_table.setItem(row, 2, QTableWidgetItem(item.get('phone', '')))
        log.debug("  Config tables populated.") # Діагностика

        # Розблоковуємо сигнали
        self.devices_table.blockSignals(False); self.zones_table.blockSignals(False)
        self.outputs_table.blockSignals(False); self.users_table.blockSignals(False)
        log.debug("  Config tables signals unblocked.") # Діагностика

        # --- ЗАМІНА: Викликаємо валідацію та оновлення властивостей ---
        self._trigger_validation() # Викликаємо валідацію після оновлення UI
        if self.current_selected_node:
            log.debug("  Triggering properties panel update after config UI update.") # Діагностика
            self.on_selection_changed() # Оновлюємо панель властивостей
        # --- КІНЕЦЬ ЗАМІНИ ---
        log.debug("Configuration UI panels update finished.") # Діагностика


    def add_device(self):
        """Обробник кнопки додавання пристрою."""
        device_type = self.device_type_combo.currentText()
        log.info(f"Adding device of type: {device_type}") # Діагностика
        # --- ВИКОРИСТАННЯ project_manager ---
        new_id = self.project_manager.add_device(device_type)
        # --- КІНЕЦЬ ---
        # Оновлення UI відбудеться через сигнал project_updated

    def remove_device(self):
        """Обробник кнопки видалення пристрою."""
        selected_rows = sorted(list(set(index.row() for index in self.devices_table.selectedIndexes())), reverse=True)
        if not selected_rows:
            log.debug("Remove device: No rows selected.") # Діагностика
            return

        ids_to_remove = []
        names_to_remove = []
        for row in selected_rows:
            id_item = self.devices_table.item(row, 0)
            name_item = self.devices_table.item(row, 1)
            if id_item:
                ids_to_remove.append(id_item.text())
                names_to_remove.append(name_item.text() if name_item else id_item.text())

        if not ids_to_remove:
            log.debug("Remove device: Could not get IDs from selected rows.") # Діагностика
            return

        log.debug(f"Attempting to remove devices: {ids_to_remove}") # Діагностика
        reply = QMessageBox.question(self, "Підтвердження видалення",
                                     f"Ви впевнені, що хочете видалити пристрої:\n - {', '.join(names_to_remove)}?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            log.debug("User confirmed device removal.") # Діагностика
            removed_count = 0
            for device_id in ids_to_remove:
                # --- ВИКОРИСТАННЯ project_manager ---
                if self.project_manager.remove_device(device_id):
                    removed_count += 1
                # --- КІНЕЦЬ ---
            log.info(f"Removed {removed_count} devices.") # Діагностика
            # Оновлення UI відбудеться через сигнал project_updated
        else:
            log.debug("User cancelled device removal.") # Діагностика


    def add_config_item(self, config_key):
        """Обробник кнопки додавання користувача."""
        if config_key != 'users': return
        log.info("Adding new user...") # Діагностика
        # --- ВИКОРИСТАННЯ project_manager ---
        new_id = self.project_manager.add_user()
        # --- КІНЕЦЬ ---
        # Оновлення UI відбудеться через сигнал project_updated

    def remove_config_item(self, config_key):
        """Обробник кнопки видалення користувача."""
        if config_key != 'users': return
        row = self.users_table.currentRow()
        log.debug(f"Attempting to remove user at row: {row}") # Діагностика
        if row > -1:
            id_item = self.users_table.item(row, 0)
            if id_item:
                user_id = id_item.text()
                log.info(f"Removing user ID: {user_id}") # Діагностика
                # --- ВИКОРИСТАННЯ project_manager ---
                self.project_manager.remove_user(user_id)
                # --- КІНЕЦЬ ---
                # Оновлення UI відбудеться через сигнал project_updated
            else:
                log.warning("Remove user: Could not get ID from selected row.") # Діагностика
        else:
            log.debug("Remove user: No row selected.") # Діагностика


    def on_config_table_changed(self, item):
        """Обробник зміни даних в таблицях конфігурації."""
        table = item.tableWidget()
        row, col = item.row(), item.column()
        id_item = table.item(row, 0)
        if not id_item:
            log.warning(f"Config table changed: No ID item found at row {row}.") # Діагностика
            return
        item_id = id_item.text()
        new_value = item.text()
        log.debug(f"Config table changed: Table={table.objectName()}, Row={row}, Col={col}, ID={item_id}, NewValue='{new_value}'") # Діагностика

        item_type = None
        data_key = None

        if table is self.devices_table and col == 1:
            item_type, data_key = 'devices', 'name'
        elif table is self.zones_table and col == 2:
            item_type, data_key = 'zones', 'name'
        elif table is self.outputs_table and col == 2:
            item_type, data_key = 'outputs', 'name'
        elif table is self.users_table:
            item_type = 'users'
            if col == 1: data_key = 'name'
            elif col == 2: data_key = 'phone'

        if item_type and data_key:
            log.debug(f"  Updating config: Type={item_type}, ID={item_id}, Key={data_key}, Value='{new_value}'") # Діагностика
            # --- ВИКОРИСТАННЯ project_manager ---
            # Передаємо emit_signal=False, оскільки оновлення UI зробить сигнал project_updated
            updated = self.project_manager.update_config_item(item_type, item_id, data_key, new_value, emit_signal=False)
            # --- КІНЕЦЬ ---
            if updated:
                 log.info(f"  Config item {item_type}/{item_id} updated.") # Діагностика
                 # Викликаємо оновлення UI ОДРАЗУ, щоб таблиця відобразила зміни
                 # і уникнути можливих конфліктів при швидкому редагуванні
                 self.update_config_ui()
                 # Якщо змінилося ім'я пристрою, зони або виходу, оновити властивості вузлів
                 if item_type in ['devices', 'zones', 'outputs'] and data_key == 'name':
                      self._update_all_items_properties()
            else:
                 log.debug("  Config item value did not change.") # Діагностика

        else:
             log.warning(f"  Unhandled config table change.") # Діагностика


    # --- Node Actions ---
    def add_node(self, node_type, position):
        log.debug(f"Attempting to add node: Type='{node_type}', Pos=({position.x():.1f}, {position.y():.1f}), Mode={self.current_edit_mode}") # Діагностика
        # Логіка перевірок (чи вибрано сценарій/макрос, чи можна додати тип) залишається тут
        if self.current_edit_mode == EDIT_MODE_SCENARIO and self.active_scenario_id is None:
            self.show_status_message("Спочатку виберіть або створіть сценарій.", 5000, color="orange")
            log.warning("  Add node aborted: No active scenario.") # Діагностика
            return
        if self.current_edit_mode == EDIT_MODE_MACRO and self.active_macro_id is None:
            self.show_status_message("Неможливо додати вузол: не вибрано макрос для редагування.", 5000, color="red")
            log.warning("  Add node aborted: No active macro.") # Діагностика
            return
        if self.current_edit_mode == EDIT_MODE_MACRO and node_type == "Тригер":
            self.show_status_message("Помилка: Тригер не можна додавати всередині макросу.", 5000, color="red")
            log.warning("  Add node aborted: Cannot add TriggerNode in macro mode.") # Діагностика
            return
        if self.current_edit_mode == EDIT_MODE_MACRO and node_type == "Макрос":
            self.show_status_message("Помилка: Макрос не можна додавати всередині іншого макросу.", 5000, color="red")
            log.warning("  Add node aborted: Cannot add MacroNode in macro mode.") # Діагностика
            return
        if node_type == "Тригер" and any(isinstance(i, TriggerNode) for i in self.scene.items()):
            self.show_status_message("Помилка: Тригер у сценарії може бути лише один.", 5000, color="red")
            log.warning("  Add node aborted: TriggerNode already exists.") # Діагностика
            return

        # Створення команди
        if node_type in NODE_REGISTRY:
            log.debug(f"  Pushing AddNodeCommand for '{node_type}'...") # Діагностика
            # --- Імпорт команди ---
            from commands import AddNodeCommand
            command = AddNodeCommand(self.scene, node_type, position)
            self.undo_stack.push(command)
        else:
            log.error(f"  Add node failed: Node type '{node_type}' not in NODE_REGISTRY.") # Діагностика

    def on_node_list_clicked(self, item):
        node_type = item.text()
        log.debug(f"Node list clicked: '{node_type}'") # Діагностика
        self.add_node(node_type, self.view.mapToScene(self.view.viewport().rect().center()))

    # --- Project Management (delegation) ---
    def new_project(self):
        log.info("MainWindow: new_project called.")
        self._initializing = True # Встановлюємо прапорець
        try:
            # --- ВИКОРИСТАННЯ project_manager ---
            self.project_manager.new_project() # Сигнал project_updated буде викликано менеджером, але обробник його проігнорує
            # --- КІНЕЦЬ ---
            self.scene.clear()
            self.undo_stack.clear()
            self.set_edit_mode(EDIT_MODE_SCENARIO) # Перемикаємо режим
            # --- ЗАМІНА: Отримуємо ID першого сценарію з менеджера ---
            first_scenario_id = self.project_manager.get_first_scenario_id()
            if first_scenario_id:
                log.debug(f"  First scenario ID: {first_scenario_id}") # Діагностика
                self.active_scenario_id = first_scenario_id # Просто встановлюємо активний ID
                self.load_scenario_state(first_scenario_id) # Завантажуємо стан одразу
            else:
                 log.warning("  No scenarios found after new project creation.") # Діагностика
                 self.active_scenario_id = None # На випадок, якщо менеджер не створив сценарій
            # --- КІНЕЦЬ ЗАМІНИ ---
            self.current_selected_node = None # Скидаємо вибір
            self.active_macro_id = None
            self.previous_scenario_id = None
            self.update_ui_from_project() # Оновлюємо UI вручну
            self.props_widget.setEnabled(False)
            self._update_window_title() # Оновлюємо заголовок
            # Валідація та оновлення зон симуляції в кінці
            self._trigger_validation()
            self._update_simulation_trigger_zones()
        finally:
            self._initializing = False # Знімаємо прапорець
        log.debug("MainWindow: new_project finished.")


    def add_scenario(self):
        log.info("Adding new scenario...") # Діагностика
        # --- ВИКОРИСТАННЯ project_manager ---
        # Викликаємо з emit_signal=False, щоб UI оновився вручну нижче
        new_name = self.project_manager.add_scenario(emit_signal=False)
        # --- КІНЕЦЬ ---
        if new_name:
            log.info(f"  Scenario '{new_name}' added by manager.") # Діагностика
            self.update_scenarios_list() # Оновлюємо список вручну
            # Вибір нового елемента
            items = self.scenarios_list.findItems(new_name, Qt.MatchFlag.MatchExactly)
            if items:
                self.scenarios_list.setCurrentItem(items[0]) # Автоматично вибираємо новий
        else:
            log.warning("  Failed to add scenario via manager.") # Діагностика


    def remove_scenario(self):
        current_item = self.scenarios_list.currentItem()
        if not current_item:
            log.debug("Remove scenario: No item selected.") # Діагностика
            return
        scenario_name = current_item.text()
        log.debug(f"Attempting to remove scenario: '{scenario_name}'") # Діагностика
        # --- ВИКОРИСТАННЯ project_manager ---
        if len(self.project_manager.get_scenario_ids()) <= 1:
            QMessageBox.warning(self, "Неможливо видалити", "Неможливо видалити останній сценарій.")
            log.warning("  Cannot remove the last scenario.") # Діагностика
            return
        # --- КІНЕЦЬ ---

        reply = QMessageBox.question(self, "Видалення Сценарію",
                                     f"Ви впевнені, що хочете видалити сценарій '{scenario_name}'?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.No:
            log.debug("  User cancelled scenario removal.") # Діагностика
            return

        log.debug("  User confirmed scenario removal.") # Діагностика
        # --- ВИКОРИСТАННЯ project_manager ---
        # Викликаємо з emit_signal=False
        removed = self.project_manager.remove_scenario(scenario_name, emit_signal=False)
        # --- КІНЕЦЬ ---
        if removed:
            log.info(f"  Scenario '{scenario_name}' removed by manager.") # Діагностика
            self.active_scenario_id = None # Скидаємо активний
            self.update_scenarios_list() # Оновлюємо список вручну
            # Логіка вибору першого сценарію вже є в update_scenarios_list
            self.undo_stack.clear() # Очищаємо історію
        else:
            log.warning(f"  Failed to remove scenario '{scenario_name}' via manager.") # Діагностика


    def remove_macro(self):
        current_item = self.macros_list.currentItem()
        if not current_item:
            log.debug("Remove macro: No item selected.") # Діагностика
            return
        macro_name = current_item.text()
        macro_id = current_item.data(Qt.ItemDataRole.UserRole)
        if not macro_id:
            log.warning("Remove macro: No macro ID found in selected item.") # Діагностика
            return
        log.debug(f"Attempting to remove macro: '{macro_name}' (ID: {macro_id})") # Діагностика

        # --- ВИКОРИСТАННЯ project_manager ---
        usage_count, usage_scenarios = self.project_manager.check_macro_usage(macro_id)
        if usage_count > 0:
            QMessageBox.warning(self, "Неможливо видалити макрос",
                                f"Макрос '{macro_name}' використовується у {usage_count} вузлах сценаріїв:\n"
                                f"{', '.join(usage_scenarios)}\nСпочатку видаліть ці вузли.")
            log.warning(f"  Cannot remove macro {macro_id}, used in {usage_count} nodes.") # Діагностика
            return
        # --- КІНЕЦЬ ---

        reply = QMessageBox.question(self, "Видалення Макросу",
                                     f"Ви впевнені, що хочете видалити макрос '{macro_name}'?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.No:
            log.debug("  User cancelled macro removal.") # Діагностика
            return

        log.debug("  User confirmed macro removal.") # Діагностика
        # --- ВИКОРИСТАННЯ project_manager ---
        # Викликаємо з emit_signal=False
        removed = self.project_manager.remove_macro(macro_id, emit_signal=False)
        # --- КІНЕЦЬ ---
        if removed:
            log.info(f"  Macro '{macro_name}' (ID: {macro_id}) removed by manager.") # Діагностика
            if self.active_macro_id == macro_id:
                log.debug("  Removed macro was active, returning to scenario.") # Діагностика
                self.return_to_scenario(force_return=True) # Повертаємось, якщо видалили поточний
            self.update_macros_list() # Оновлюємо список вручну
            self._update_all_items_properties() # Оновити MacroNode, якщо вони стали невалідними
        else:
            log.warning(f"  Failed to remove macro '{macro_name}' via manager.") # Діагностика

    def rename_macro(self):
        current_item = self.macros_list.currentItem()
        if not current_item:
             log.debug("Rename macro: No item selected.") # Діагностика
             return
        macro_id = current_item.data(Qt.ItemDataRole.UserRole)
        if not macro_id:
             log.warning("Rename macro: No macro ID found in selected item.") # Діагностика
             return

        # --- ВИКОРИСТАННЯ project_manager ---
        macro_data = self.project_manager.get_macro_data(macro_id)
        current_name = macro_data.get('name', macro_id) if macro_data else macro_id
        # --- КІНЕЦЬ ---
        log.debug(f"Attempting to rename macro: ID={macro_id}, CurrentName='{current_name}'") # Діагностика

        new_name, ok = QInputDialog.getText(self, "Перейменувати Макрос",
                                            "Нове ім'я макросу:", QLineEdit.EchoMode.Normal, current_name)

        if ok and new_name.strip():
            new_name_stripped = new_name.strip()
            log.debug(f"  User entered new name: '{new_name_stripped}'") # Діагностика
            # --- ВИКОРИСТАННЯ project_manager ---
            # Викликаємо з emit_signal=False
            success = self.project_manager.rename_macro(macro_id, new_name_stripped, emit_signal=False)
            # --- КІНЕЦЬ ---
            if success:
                log.info(f"  Macro {macro_id} renamed to '{new_name_stripped}'.") # Діагностика
                self.update_macros_list() # Оновлюємо список вручну
                self._update_all_items_properties() # Оновити MacroNode на сцені
                if self.active_macro_id == macro_id:
                     log.debug("  Active macro was renamed, updating window title.") # Діагностика
                     self._update_window_title() # Оновити заголовок
            else:
                 log.warning(f"  Failed to rename macro {macro_id} to '{new_name_stripped}'.") # Діагностика
                 QMessageBox.warning(self, "Помилка", "Не вдалося перейменувати макрос (можливо, ім'я вже існує).")
        else:
            log.debug("  Macro rename cancelled by user or empty name entered.") # Діагностика


    # --- Обробники подій списків ---
    def on_scenario_item_double_clicked(self, item):
        log.debug(f"Scenario item double-clicked: '{item.text()}'") # Діагностика
        # Логіка без змін
        if self.current_edit_mode == EDIT_MODE_MACRO:
            log.debug("  In macro mode, attempting return to scenario first.") # Діагностика
            if not self.return_to_scenario(): return
        self._old_scenario_name = item.text()
        log.debug(f"  Stored old name: '{self._old_scenario_name}', enabling edit.") # Діагностика
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        self.scenarios_list.editItem(item)

    def on_macro_item_double_clicked(self, item):
        macro_id = item.data(Qt.ItemDataRole.UserRole)
        log.debug(f"Macro item double-clicked: '{item.text()}' (ID: {macro_id})") # Діагностика
        # Логіка без змін
        if macro_id:
            self.edit_macro(macro_id)

    def on_scenario_renamed(self, item):
        # Цей метод обробляє ЗАВЕРШЕННЯ редагування в QListWidget
        new_name = item.text().strip()
        old_name = self._old_scenario_name
        log.debug(f"Scenario item finished editing. Old='{old_name}', New='{new_name}'") # Діагностика
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable) # Знімаємо флаг

        if not new_name or not old_name or new_name == old_name:
            if old_name: item.setText(old_name) # Повертаємо старе ім'я
            log.debug("  Rename aborted (empty, no change, or no old name).") # Діагностика
            self._old_scenario_name = None
            return

        log.debug(f"  Attempting rename via project manager: '{old_name}' -> '{new_name}'") # Діагностика
        # --- ВИКОРИСТАННЯ project_manager ---
        # Викликаємо з emit_signal=False
        success = self.project_manager.rename_scenario(old_name, new_name, emit_signal=False)
        # --- КІНЕЦЬ ---

        if not success:
            log.warning("  Rename failed (likely name exists). Reverting UI.") # Діагностика
            QMessageBox.warning(self, "Помилка перейменування", f"Не вдалося перейменувати сценарій '{old_name}' (можливо, ім'я '{new_name}' вже існує).")
            item.setText(old_name) # Повертаємо старе ім'я в UI
        else:
            log.info(f"  Scenario '{old_name}' successfully renamed to '{new_name}'.") # Діагностика
            if self.active_scenario_id == old_name:
                log.debug("  Active scenario was renamed, updating active ID and title.") # Діагностика
                self.active_scenario_id = new_name # Оновлюємо активний ID
                self._update_window_title() # Оновлюємо заголовок вікна
            self.update_scenarios_list() # Оновлюємо список вручну

        self._old_scenario_name = None # Скидаємо збережене старе ім'я

    def on_active_scenario_changed(self, current_item, previous_item):
        # Логіка зміни сценарію (збереження попереднього, завантаження поточного)
        # --- МАЙЖЕ БЕЗ ЗМІН, але використовує save_current_state/load_scenario_state ---
        current_text = current_item.text() if current_item else "None"
        prev_text = previous_item.text() if previous_item else "None"
        log.debug(f"MW: on_active_scenario_changed. Prev: '{prev_text}', Current: '{current_text}'") # Діагностика

        if self._initializing or self._loading_project: # Додано перевірку прапорців
            log.debug("  Ignoring active scenario change during init/load.")
            return

        if self.current_edit_mode == EDIT_MODE_MACRO:
             log.debug("  Ignoring scenario change in macro mode.") # Діагностика
             # Відновлення вибору в списку, якщо потрібно
             if self.previous_scenario_id:
                 items = self.scenarios_list.findItems(self.previous_scenario_id, Qt.MatchFlag.MatchExactly)
                 if items:
                      log.debug(f"  Re-selecting previous scenario '{self.previous_scenario_id}' in list.") # Діагностика
                      self.scenarios_list.blockSignals(True)
                      self.scenarios_list.setCurrentItem(items[0])
                      self.scenarios_list.blockSignals(False)
             return

        if self.simulator.is_running:
            log.debug("  Stopping simulation due to scenario change.") # Діагностика
            self.stop_simulation()

        # Зберігаємо попередній стан (якщо він був і це був сценарій)
        if previous_item:
            prev_id = previous_item.text()
            # Перевіряємо, чи існує сценарій з таким ID в менеджері
            if self.project_manager.get_scenario_data(prev_id):
                 log.debug(f"  Saving state for previous scenario: {prev_id}") # Діагностика
                 self.save_current_state() # Збереже поточний активний сценарій
            else:
                 log.warning(f"  Previous scenario '{prev_id}' not found in manager, not saving state.") # Діагностика

        # Завантажуємо новий стан
        if current_item:
            new_active_id = current_item.text()
            # Перевірка, чи не завантажуємо той самий сценарій знову
            if self.active_scenario_id == new_active_id and self.scene.items():
                log.debug(f"  Scenario '{new_active_id}' is already active and loaded. Skipping reload.")
            else:
                log.debug(f"  Loading state for new scenario: {new_active_id}") # Діагностика
                self.load_scenario_state(new_active_id) # Завантажить дані та оновить self.active_scenario_id
        else:
            log.debug("  No current item selected, clearing scene.") # Діагностика
            if self.active_scenario_id is not None: # Очищаємо, тільки якщо щось було активне
                self.scene.clear()
                self.active_scenario_id = None
                self._update_window_title()

        self.undo_stack.clear()
        self.current_selected_node = None
        self.on_selection_changed() # Оновити панель властивостей
        log.debug("MW: on_active_scenario_changed finished.")


    # --- Edit Mode Management ---
    def set_edit_mode(self, mode):
        # Логіка без змін, вона не залежить від project_data
        if self.current_edit_mode == mode: return
        mode_str = 'MACRO' if mode == EDIT_MODE_MACRO else 'SCENARIO'
        log.info(f"Switching edit mode to {mode_str}") # Діагностика
        self.current_edit_mode = mode
        self.undo_stack.clear()
        self._update_node_toolbars() # Оновлюємо ТІЛЬКИ динамічні тулбари
        self._update_window_title()
        self._update_nodes_list()
        # self.sim_toolbar.setVisible(mode == EDIT_MODE_SCENARIO) # Це робиться в _update_node_toolbars
        self.scene.clearSelection()
        self.current_selected_node = None
        self.on_selection_changed()

    def edit_macro(self, macro_id):
        log.debug(f"Attempting to edit macro: {macro_id}") # Діагностика
        # --- ВИКОРИСТАННЯ project_manager ---
        macro_data = self.project_manager.get_macro_data(macro_id)
        if not macro_data:
        # --- КІНЕЦЬ ---
            log.error(f"Cannot edit macro: ID {macro_id} not found.")
            return

        if self.current_edit_mode == EDIT_MODE_MACRO and self.active_macro_id == macro_id:
            log.debug("  Already editing this macro.") # Діагностика
            return

        log.debug("  Saving current state before switching to macro edit.") # Діагностика
        self.save_current_state() # Зберігаємо поточний стан (сценарію)
        self.previous_scenario_id = self.active_scenario_id # Запам'ятовуємо сценарій
        log.debug(f"  Stored previous scenario ID: {self.previous_scenario_id}") # Діагностика

        # Перемикаємо режим та завантажуємо стан макросу
        self.set_edit_mode(EDIT_MODE_MACRO)
        self.load_macro_state(macro_id) # Це оновить active_macro_id та заголовок

        # Оновлення UI списків
        self.project_tabs.setCurrentIndex(1) # Перемкнути на вкладку макросів
        macro_name = macro_data.get('name', macro_id) # Отримуємо актуальне ім'я
        items = self.macros_list.findItems(macro_name, Qt.MatchFlag.MatchExactly)
        if items:
            log.debug(f"  Selecting macro '{macro_name}' in list.") # Діагностика
            self.macros_list.blockSignals(True)
            self.macros_list.setCurrentItem(items[0])
            self.macros_list.blockSignals(False)
        else:
            log.warning(f"  Could not find macro '{macro_name}' in list after loading.") # Діагностика

    def return_to_scenario(self, force_return=False):
        log.debug("Attempting to return to scenario...") # Діагностика
        # --- МАЙЖЕ БЕЗ ЗМІН, але використовує save_current_state/load_scenario_state ---
        if self.current_edit_mode != EDIT_MODE_MACRO:
            log.debug("  Already in scenario mode.") # Діагностика
            return True

        log.debug("  Saving current macro state...") # Діагностика
        self.save_current_state() # Зберігаємо стан макросу

        scenario_to_load = self.previous_scenario_id
        log.debug(f"  Scenario to return to: {scenario_to_load}") # Діагностика
        self.previous_scenario_id = None # Скидаємо
        # active_macro_id скинеться в load_scenario_state
        self.set_edit_mode(EDIT_MODE_SCENARIO) # Перемикаємо режим

        # Завантажуємо попередній сценарій або перший доступний
        scenario_found_to_load = False
        if scenario_to_load and self.project_manager.get_scenario_data(scenario_to_load):
            log.debug(f"  Loading previous scenario: {scenario_to_load}") # Діагностика
            self.load_scenario_state(scenario_to_load)
            scenario_found_to_load = True
        else:
            log.warning(f"  Previous scenario '{scenario_to_load}' not found or invalid.") # Діагностика
            first_id = self.project_manager.get_first_scenario_id()
            if first_id:
                log.debug(f"  Loading first available scenario: {first_id}") # Діагностика
                self.load_scenario_state(first_id)
                scenario_found_to_load = True
            else: # Якщо сценаріїв взагалі немає
                log.warning("  No scenarios available to load.") # Діагностика
                self.scene.clear()
                self.active_scenario_id = None
                self._update_window_title()

        # Оновлення UI списків
        self.project_tabs.setCurrentIndex(0) # Перемкнути на вкладку сценаріїв
        if scenario_found_to_load and self.active_scenario_id:
            items = self.scenarios_list.findItems(self.active_scenario_id, Qt.MatchFlag.MatchExactly)
            if items:
                log.debug(f"  Selecting scenario '{self.active_scenario_id}' in list.") # Діагностика
                self.scenarios_list.blockSignals(True)
                self.scenarios_list.setCurrentItem(items[0])
                self.scenarios_list.blockSignals(False)
        else:
            log.warning("  Could not select any scenario in the list after returning.") # Діагностика
        return True

    # --- Scene State Save/Load ---
    def save_current_state(self):
        """Зберігає поточний стан сцени в ProjectManager."""
        log.debug(f"MW: Saving current state (Mode: {self.current_edit_mode}, Scenario: {self.active_scenario_id}, Macro: {self.active_macro_id})") # Діагностика
        # --- ВИКОРИСТАННЯ scene_utils ---
        scene_data = extract_data_from_scene(self.scene)
        # --- КІНЕЦЬ ---
        if self.current_edit_mode == EDIT_MODE_SCENARIO and self.active_scenario_id:
            log.debug(f"  Saving data for scenario: {self.active_scenario_id}") # Діагностика
            # --- ВИКОРИСТАННЯ project_manager ---
            # Передаємо emit_signal=False, щоб уникнути зайвих оновлень UI
            self.project_manager.update_scenario_data(self.active_scenario_id, scene_data, emit_signal=False)
            # --- КІНЕЦЬ ---
        elif self.current_edit_mode == EDIT_MODE_MACRO and self.active_macro_id:
            log.debug(f"  Saving data for macro: {self.active_macro_id}") # Діагностика
            # --- ВИКОРИСТАННЯ project_manager ---
            # Передаємо emit_signal=False
            updated_macro_data = self.project_manager.update_macro_data(self.active_macro_id, scene_data, emit_signal=False)
            # --- КІНЕЦЬ ---
            if updated_macro_data: # Якщо змінились входи/виходи макросу
                 log.info("  Macro IO definition changed, updating MacroNodes in scenarios...") # Діагностика
                 self.update_macro_nodes_in_scenarios(self.active_macro_id)
            else:
                 log.debug("  Macro IO definition did not change.") # Діагностика
        else:
            log.warning("save_current_state: No active scenario or macro to save.")

    def load_scenario_state(self, scenario_id):
        """Завантажує стан сценарію зі ProjectManager на сцену."""
        log.info(f"MW: Loading scenario state for '{scenario_id}'") # Діагностика
        # --- ВИКОРИСТАННЯ project_manager ---
        scenario_data = self.project_manager.get_scenario_data(scenario_id)
        macros_data = self.project_manager.get_macros_data()
        # --- КІНЕЦЬ ---
        if scenario_data:
            self.scene.clear()
            log.debug("  Scene cleared.") # Діагностика
            # --- ВИКОРИСТАННЯ scene_utils ---
            log.debug("  Populating scene from data...") # Діагностика
            populate_scene_from_data(self.scene, scenario_data, self.view, macros_data)
            # --- КІНЕЦЬ ---
            self.active_scenario_id = scenario_id
            self.active_macro_id = None # Ми в режимі сценарію
            self._update_window_title()
            # --- ЗМІНА: Валідація та оновлення зон викликаються після завантаження ---
            # self._trigger_validation() # Викликаємо не тут, а в кінці new_project/import/on_active_scenario_changed
            # self._update_simulation_trigger_zones()
            # --- КІНЕЦЬ ЗМІНИ ---
            log.info(f"Scenario '{scenario_id}' loaded successfully.") # Діагностика
        else:
            log.error(f"MW: Scenario data not found for '{scenario_id}'") # Діагностика
            self.scene.clear() # Очищаємо сцену, якщо дані не знайдено
            self.active_scenario_id = None
            self._update_window_title()

    def load_macro_state(self, macro_id):
        """Завантажує стан макросу зі ProjectManager на сцену."""
        log.info(f"MW: Loading macro state for ID: {macro_id}") # Діагностика
        # --- ВИКОРИСТАННЯ project_manager ---
        macro_data = self.project_manager.get_macro_data(macro_id)
        # --- КІНЕЦЬ ---
        if macro_data:
            self.scene.clear()
            log.debug("  Scene cleared.") # Діагностика
            # --- ВИКОРИСТАННЯ scene_utils ---
            log.debug("  Populating scene from data...") # Діагностика
            populate_scene_from_data(self.scene, macro_data, self.view) # Не передаємо макроси всередину
            # --- КІНЕЦЬ ---
            self.active_scenario_id = None # Ми в режимі макросу
            self.active_macro_id = macro_id
            self._update_window_title()
            # --- ЗМІНА: Валідація викликається після завантаження ---
            # self._trigger_validation() # Викликаємо не тут
            # --- КІНЕЦЬ ЗМІНИ ---
            log.info(f"Macro {macro_id} ('{macro_data.get('name', '?')}') loaded successfully.") # Діагностика
        else:
            log.error(f"MW: Macro data not found for ID: {macro_id}") # Діагностика
            self.scene.clear()
            self.active_macro_id = None
            self._update_window_title()

    def update_macro_nodes_in_scenarios(self, updated_macro_id):
        """Оновлює MacroNode на поточній сцені, якщо вона є сценарієм."""
        log.info(f"MW: Updating MacroNodes for macro {updated_macro_id} on current scene (if applicable).") # Діагностика
        if self.current_edit_mode != EDIT_MODE_SCENARIO:
             log.debug("  Skipping update, not in scenario mode.") # Діагностика
             return # Оновлюємо тільки якщо зараз відкрито сценарій

        # --- ВИКОРИСТАННЯ project_manager ---
        macro_data = self.project_manager.get_macro_data(updated_macro_id)
        # --- КІНЕЦЬ ---
        if not macro_data:
             log.warning(f"  Cannot update MacroNodes, definition for {updated_macro_id} not found.") # Діагностика
             return

        needs_validation = False
        log.debug("  Iterating through scene items to find matching MacroNodes...") # Діагностика
        for item in self.scene.items():
            if isinstance(item, MacroNode) and item.macro_id == updated_macro_id:
                log.debug(f"  Updating sockets for MacroNode {item.id} on current scene.") # Діагностика
                item.update_sockets_from_definition(macro_data)
                # TODO: Перевірити/видалити невалідні з'єднання після оновлення сокетів? (Валідація має це зробити)
                needs_validation = True

        if needs_validation:
             log.debug("  MacroNodes updated, triggering validation.") # Діагностика
             self._trigger_validation() # Запускаємо валідацію, якщо були зміни
        else:
             log.debug("  No matching MacroNodes found on current scene.") # Діагностика

    # --- UI Updates ---
    def update_ui_from_project(self):
        """Оновлює списки сценаріїв, макросів та таблиці конфігурації."""
        log.debug("MW: Updating all UI from project data...") # Діагностика
        self.update_scenarios_list()
        self.update_macros_list()
        self.update_config_ui() # Цей метод тепер просто відображає дані менеджера
        self._update_nodes_list()
        # --- ЗМІНА: Валідація та оновлення зон викликаються тут, після оновлення UI ---
        self._trigger_validation()
        self._update_simulation_trigger_zones()
        # --- КІНЕЦЬ ЗМІНИ ---
        log.debug("MW: UI update from project data finished.") # Діагностика

    def update_scenarios_list(self):
        """Оновлює список сценаріїв у UI."""
        log.debug("Updating scenarios list widget...") # Діагностика
        # Зберігаємо поточний вибір АБО активний ID
        current_text_selected = self.scenarios_list.currentItem().text() if self.scenarios_list.currentItem() else None
        id_to_select = current_text_selected or self.active_scenario_id
        log.debug(f"  Current selection/active ID to preserve: {id_to_select}") # Діагностика

        self.scenarios_list.blockSignals(True)
        self.scenarios_list.clear()
        # --- ВИКОРИСТАННЯ project_manager ---
        scenario_ids = self.project_manager.get_scenario_ids()
        # --- КІНЕЦЬ ---
        log.debug(f"  Populating list with {len(scenario_ids)} scenarios.") # Діагностика
        for name in scenario_ids:
            item = QListWidgetItem(name)
            self.scenarios_list.addItem(item)

        selected_item = None
        if id_to_select:
            items = self.scenarios_list.findItems(id_to_select, Qt.MatchFlag.MatchExactly)
            if items:
                selected_item = items[0]
                log.debug(f"  Found item to re-select: '{id_to_select}'") # Діагностика

        # Якщо активний сценарій не знайдено, вибираємо перший
        if not selected_item and scenario_ids:
             first_id = scenario_ids[0]
             items = self.scenarios_list.findItems(first_id, Qt.MatchFlag.MatchExactly)
             if items:
                 selected_item = items[0]
                 log.debug(f"  Re-selection failed, selecting first item: '{first_id}'") # Діагностика
                 # Якщо вибрали перший, бо попередній зник/не знайдено,
                 # і це відрізняється від поточного активного ID,
                 # ТРЕБА завантажити його стан
                 if self.active_scenario_id != first_id:
                      log.debug(f"  Loading state for newly selected first scenario: {first_id}") # Діагностика
                      # Встановлюємо активний ID одразу, load_scenario_state зробить решту
                      self.active_scenario_id = first_id
                      QTimer.singleShot(0, lambda sid=first_id: self.load_scenario_state(sid)) # Завантажуємо з невеликою затримкою

        if selected_item:
             log.debug(f"  Setting current item in list: '{selected_item.text()}'") # Діагностика
             self.scenarios_list.setCurrentItem(selected_item)
             # Якщо вибраний елемент відповідає активному ID, але сцена порожня (після new_project/import)
             if self.active_scenario_id == selected_item.text() and not self.scene.items():
                 log.debug(f"  Scene is empty but active scenario matches selection. Loading state for {self.active_scenario_id}") # Діагностика
                 # Викликаємо одразу, бо це частина ініціалізації/оновлення
                 self.load_scenario_state(self.active_scenario_id)

        elif not scenario_ids: # Якщо сценаріїв взагалі немає
            log.debug("  No scenarios in the list. Clearing scene and active ID.") # Діагностика
            if self.active_scenario_id is not None:
                self.scene.clear()
                self.active_scenario_id = None
                self._update_window_title()

        self.scenarios_list.blockSignals(False)
        log.debug("Scenarios list widget update finished.") # Діагностика


    def update_macros_list(self):
        """Оновлює список макросів у UI."""
        log.debug("Updating macros list widget...") # Діагностика
        current_id_selected = None
        current_item = self.macros_list.currentItem()
        if current_item:
            current_id_selected = current_item.data(Qt.ItemDataRole.UserRole)
        log.debug(f"  Current macro ID selected in list: {current_id_selected}") # Діагностика

        self.macros_list.blockSignals(True)
        self.macros_list.clear()
        # --- ВИКОРИСТАННЯ project_manager ---
        macros_data = self.project_manager.get_macros_data()
        # --- КІНЕЦЬ ---
        log.debug(f"  Populating list with {len(macros_data)} macros.") # Діагностика
        new_item_to_select = None
        # Сортуємо за іменем
        sorted_macros = sorted(macros_data.items(), key=lambda item: item[1].get('name', item[0]))

        for macro_id, macro_info in sorted_macros:
            item = QListWidgetItem(macro_info.get('name', macro_id))
            item.setData(Qt.ItemDataRole.UserRole, macro_id)
            self.macros_list.addItem(item)
            if macro_id == current_id_selected:
                new_item_to_select = item
                log.debug(f"  Found item to re-select: '{item.text()}' (ID: {macro_id})") # Діагностика

        if new_item_to_select:
            log.debug(f"  Setting current item in list: '{new_item_to_select.text()}'") # Діагностика
            self.macros_list.setCurrentItem(new_item_to_select)
        elif self.macros_list.count() > 0 and self.current_edit_mode == EDIT_MODE_MACRO:
             # Якщо редагували макрос, який видалили, вибираємо перший у списку? НІ.
             log.debug("  No item re-selected. If in macro mode and active macro removed, handle return to scenario elsewhere.") # Діагностика
             pass # Не вибираємо нічого, щоб не ініціювати завантаження іншого макросу

        self.macros_list.blockSignals(False)

        # Оновлюємо комбо-бокс у властивостях, якщо вибрано MacroNode
        if self.current_selected_node and isinstance(self.current_selected_node, MacroNode):
            log.debug("  Current node is MacroNode, updating properties panel UI for definition list.") # Діагностика
            self._update_properties_panel_ui() # Перезаповнить комбо-бокс
        log.debug("Macros list widget update finished.") # Діагностика

    def _update_all_items_properties(self):
        """Оновлює відображення властивостей для всіх вузлів на сцені."""
        log.info("MW: Updating display properties for all nodes on scene.") # Діагностика
        config_data = self.project_manager.get_config_data()
        for item in self.scene.items():
            if isinstance(item, BaseNode):
                try: # Додаємо try-except для надійності
                     item.update_display_properties(config_data)
                except Exception as e:
                     log.error(f"  Error updating display properties for node {getattr(item, 'id', '?')}: {e}", exc_info=True) # Діагностика
        self._trigger_validation() # Викликаємо валідацію після оновлення

    # --- Validation ---
    def validate_current_view(self):
        """Викликає відповідну функцію валідації."""
        if self._loading_project or self._initializing: # Додано перевірку прапорців
             log.debug("Validation skipped (loading/initializing).")
             return
        log.info(f"MW: Validating current view (Mode: {self.current_edit_mode}).") # Діагностика
        config_data = self.project_manager.get_config_data() # Отримуємо актуальну конфігурацію
        if self.current_edit_mode == EDIT_MODE_SCENARIO:
            log.debug("  Validating as SCENARIO...") # Діагностика
            # --- ВИКОРИСТАННЯ validation.py ---
            validate_scenario_on_scene(self.scene, config_data)
            # --- КІНЕЦЬ ---
        elif self.current_edit_mode == EDIT_MODE_MACRO:
            log.debug("  Validating as MACRO...") # Діагностика
            # --- ВИКОРИСТАННЯ validation.py ---
            validate_macro_on_scene(self.scene, config_data)
            # --- КІНЕЦЬ ---
        log.debug("  Validation finished.") # Діагностика

    # --- Simulation ---
    def _update_simulation_trigger_zones(self):
        log.debug("Updating simulation trigger zones combo box...") # Діагностика
        if self._loading_project or self._initializing: # Додано перевірку прапорців
            log.debug("  Skipped simulation zones update (loading/initializing).")
            return
        # Логіка не змінилася, але використовує project_manager
        if self.current_edit_mode != EDIT_MODE_SCENARIO:
            log.debug("  Not in scenario mode, clearing combo.") # Діагностика
            self.sim_trigger_zone_combo.clear()
            self.sim_trigger_zone_combo.addItem("Недоступно в режимі макросу", userData=None)
            self.update_simulation_controls(); return

        current_data = self.sim_trigger_zone_combo.currentData() # Зберігаємо поточний вибір
        self.sim_trigger_zone_combo.blockSignals(True) # Блокуємо сигнали
        self.sim_trigger_zone_combo.clear()
        trigger_node = next((item for item in self.scene.items() if isinstance(item, TriggerNode)), None)

        if not trigger_node:
            log.warning("  TriggerNode not found on scene.") # Діагностика
            self.sim_trigger_zone_combo.addItem("Тригер не знайдено", userData=None)
        else:
            props = dict(trigger_node.properties)
            zone_ids = props.get('zones', [])
            log.debug(f"  TriggerNode zones: {zone_ids}") # Діагностика

            if not zone_ids:
                log.warning("  TriggerNode has no zones assigned.") # Діагностика
                self.sim_trigger_zone_combo.addItem("Немає зон в тригері", userData=None)
            else:
                # --- ВИКОРИСТАННЯ project_manager ---
                all_zones, _ = self.project_manager.get_all_zones_and_outputs()
                # --- КІНЕЦЬ ---
                found_zones = False
                log.debug(f"  Populating combo with {len(zone_ids)} trigger zones (from {len(all_zones)} total zones)...") # Діагностика
                for zid in zone_ids:
                    zone_found_in_config = False
                    for z in all_zones:
                        if z['id'] == zid:
                            item_text = f"{z.get('parent_name', '?')}: {z['name']}"
                            log.debug(f"    Adding zone: '{item_text}' (ID: {zid})") # Діагностика
                            self.sim_trigger_zone_combo.addItem(item_text, userData=zid)
                            found_zones = True
                            zone_found_in_config = True
                            break
                    if not zone_found_in_config:
                         log.warning(f"    Trigger zone ID '{zid}' not found in current config.") # Діагностика

                if not found_zones:
                    log.warning("  None of the trigger zones were found in the config.") # Діагностика
                    self.sim_trigger_zone_combo.addItem("Призначені зони не знайдено", userData=None)

        # Відновлюємо вибір, якщо можливо
        index_to_restore = self.sim_trigger_zone_combo.findData(current_data)
        if index_to_restore != -1:
             self.sim_trigger_zone_combo.setCurrentIndex(index_to_restore)
             log.debug(f"  Restored selection to index {index_to_restore}") # Діагностика
        elif self.sim_trigger_zone_combo.count() > 0:
             self.sim_trigger_zone_combo.setCurrentIndex(0) # Вибираємо перший, якщо попередній зник
             log.debug("  Restored selection to index 0 (first available)") # Діагностика

        self.sim_trigger_zone_combo.blockSignals(False) # Розблоковуємо сигнали
        self.update_simulation_controls()

    def update_simulation_controls(self):
        log.debug("Updating simulation control buttons enabled state...") # Діагностика
        # Логіка без змін
        sim_enabled = self.current_edit_mode == EDIT_MODE_SCENARIO
        is_ready_for_sim = False
        if sim_enabled and self.scene:
            trigger_node = next((item for item in self.scene.items() if isinstance(item, TriggerNode)), None)
            # Перевіряємо, чи тригер існує, чи немає помилок валідації, і чи вибрана валідна зона
            if trigger_node and not trigger_node.error_icon.isVisible():
                if self.sim_trigger_zone_combo.count() > 0 and self.sim_trigger_zone_combo.currentData() is not None:
                     is_ready_for_sim = True
        is_running = self.simulator.is_running
        log.debug(f"  SimEnabled={sim_enabled}, ReadyForSim={is_ready_for_sim}, IsRunning={is_running}") # Діагностика
        self.start_sim_action.setEnabled(sim_enabled and is_ready_for_sim and not is_running)
        self.step_sim_action.setEnabled(sim_enabled and is_running)
        self.stop_sim_action.setEnabled(sim_enabled and is_running)
        self.sim_trigger_zone_combo.setEnabled(sim_enabled and not is_running)

    def start_simulation(self):
        log.info("Start simulation button clicked.") # Діагностика
        # Логіка без змін
        if self.current_edit_mode != EDIT_MODE_SCENARIO: return
        self.validate_current_view() # Перевіряємо помилки перед запуском
        QApplication.processEvents() # Обробляємо події, щоб валідація завершилась
        log.debug("  Checking for validation errors before starting simulation...") # Діагностика
        has_errors = False
        for item in self.scene.items():
            if isinstance(item, BaseNode) and item.error_icon.isVisible():
                log.error(f"  Validation error found on node {item.id}: {item.error_icon.toolTip()}") # Діагностика
                has_errors = True
        if has_errors:
            self.show_status_message("Помилка: Неможливо почати симуляцію, у сценарії є помилки.", 5000, color="red")
            return

        trigger_zone_id = self.sim_trigger_zone_combo.currentData()
        log.info(f"  Starting simulation with trigger zone ID: {trigger_zone_id}") # Діагностика
        if self.simulator.start(trigger_zone_id):
            self.view.set_interactive(False)
            self.update_simulation_controls()
            log.debug("  Simulation started successfully.") # Діагностика
        else:
            log.error("  Simulator failed to start.") # Діагностика


    def step_simulation(self):
        log.debug("Step simulation button clicked.") # Діагностика
        # Логіка без змін
        if self.current_edit_mode != EDIT_MODE_SCENARIO: return
        self.simulator.step()
        if not self.simulator.is_running: # Симулятор сам зупинився
            log.info("  Simulation finished after step.") # Діагностика
            # self.show_status_message("Симуляція завершена.", color="lime") # Повідомлення вже є
            self.stop_simulation() # Оновлюємо UI
        else:
            self.update_simulation_controls()

    def stop_simulation(self):
        log.info("Stop simulation button clicked.") # Діагностика
        # Логіка без змін
        self.simulator.stop()
        self.view.set_interactive(True)
        self.update_simulation_controls()
        log.debug("  Simulation stopped and view set to interactive.") # Діагностика

    def get_user_choice_for_condition(self, node):
        log.debug(f"Getting user choice for condition node {node.id} ('{node.node_name}')") # Діагностика
        # Логіка без змін, але використовує project_manager
        props = dict(node.properties)
        zone_id = props.get('zone_id')
        zone_name = "Невідома зона"
        # --- ВИКОРИСТАННЯ project_manager ---
        all_zones, _ = self.project_manager.get_all_zones_and_outputs()
        # --- КІНЕЦЬ ---
        for z in all_zones:
            if z['id'] == zone_id:
                zone_name = f"'{z.get('parent_name', '?')}: {z['name']}'"
                break
        log.debug(f"  Condition zone: {zone_name} (ID: {zone_id})") # Діагностика
        items = ["Під охороною", "Знята з охорони", "Тривога"]
        item, ok = QInputDialog.getItem(self, "Симуляція: Вузол 'Умова'",
                                        f"Який поточний стан зони {zone_name}?",
                                        items, 0, False)
        result = item if ok and item else None
        log.debug(f"  User choice: {result} (OK={ok})") # Діагностика
        return result

    # --- Clipboard ---
    def copy_selection(self):
        log.debug("Copy selection triggered.") # Діагностика
        # --- ВИКОРИСТАННЯ clipboard.py ---
        copied_count = copy_selection_to_clipboard(self.scene)
        if copied_count > 0:
            self.show_status_message(f"Скопійовано {copied_count} елемент(и).")
        # --- КІНЕЦЬ ---

    def paste_at_center(self):
        log.debug("Paste at center triggered.") # Діагностика
        self.paste_selection()

    def paste_selection(self, view_pos=None):
        paste_target_pos = view_pos or self.view.viewport().rect().center()
        paste_scene_pos = self.view.mapToScene(paste_target_pos)
        log.debug(f"Paste selection triggered at scene pos ({paste_scene_pos.x():.1f}, {paste_scene_pos.y():.1f})") # Діагностика
        # --- ВИКОРИСТАННЯ clipboard.py ---
        success = paste_selection_from_clipboard(
            self.scene, paste_scene_pos, self.view, self.current_edit_mode, self.undo_stack
        )
        if not success:
             log.warning("  Paste operation failed.") # Діагностика
             self.show_status_message("Помилка вставки з буферу обміну.", 5000, color="red")
        else:
             log.debug("  Paste command pushed successfully.") # Діагностика
        # --- КІНЕЦЬ ---

    # --- Import / Export ---
    def import_project(self):
        path, _ = QFileDialog.getOpenFileName(self, "Імпорт проекту", "", "XML Files (*.xml)")
        if not path:
            log.debug("Import cancelled by user.") # Діагностика
            return
        log.info(f"Starting project import from: {path}") # Діагностика
        self._loading_project = True # Встановлюємо прапорець
        try:
            # --- ВИКОРИСТАННЯ serialization.py ---
            new_project_data = import_project_data(path)
            # --- КІНЕЦЬ ---
            if new_project_data is None:
                log.error("Failed to load project data from file.") # Діагностика
                # QMessageBox показується в import_project_data
                return

            log.debug("Project data loaded from file, loading into manager...") # Діагностика
            # --- ВИКОРИСТАННЯ project_manager ---
            # load_project викличе project_updated, але обробник його проігнорує
            self.project_manager.load_project(new_project_data)
            # --- КІНЕЦЬ ---
            self.scene.clear()
            self.undo_stack.clear()
            self.set_edit_mode(EDIT_MODE_SCENARIO) # Завжди починаємо зі сценаріїв
            self.current_selected_node = None

            # Завантажуємо перший сценарій, якщо він є
            first_id = self.project_manager.get_first_scenario_id()
            if first_id:
                 log.debug(f"  Loading first scenario after import: {first_id}") # Діагностика
                 # Встановлюємо ID, load_scenario_state буде викликано з update_ui_from_project
                 self.active_scenario_id = first_id
                 self.load_scenario_state(first_id) # Завантажуємо стан одразу
            else:
                 log.warning("  No scenarios found in imported project.") # Діагностика
                 self.active_scenario_id = None # Немає сценаріїв

            self.update_ui_from_project() # Оновлюємо весь UI вручну
            self.props_widget.setEnabled(False)
            self._update_window_title()
            # Валідація та оновлення зон симуляції в кінці
            self._trigger_validation()
            self._update_simulation_trigger_zones()

            self.show_status_message(f"Проект успішно імпортовано з {path}", color="green")
            log.info("Project imported successfully.") # Діагностика

        except Exception as e:
            log.critical(f"An unhandled exception occurred during project import: {e}", exc_info=True) # Діагностика
            QMessageBox.critical(self, "Помилка імпорту", f"Не вдалося імпортувати проект:\n{e}")
            log.info("Resetting to new project after import error.") # Діагностика
            self.new_project() # Скидаємо до нового проекту при критичній помилці
        finally:
            self._loading_project = False # Знімаємо прапорець

    def export_project(self):
        log.debug("Export project triggered.") # Діагностика
        log.debug("  Saving current state before export...") # Діагностика
        self.save_current_state() # Зберігаємо поточний стан перед експортом
        path, _ = QFileDialog.getSaveFileName(self, "Експорт проекту", "", "XML Files (*.xml)")
        if not path:
            log.debug("Export cancelled by user.") # Діагностика
            return
        log.info(f"Starting project export to: {path}") # Діагностика
        try:
            # --- ВИКОРИСТАННЯ serialization.py та project_manager ---
            project_data_to_save = self.project_manager.get_project_data()
            log.debug("  Got project data from manager for export.") # Діагностика
            success = export_project_data(path, project_data_to_save)
            # --- КІНЕЦЬ ---
            if success:
                self.show_status_message(f"Проект успішно експортовано до {path}", color="green")
                log.info("Project exported successfully.") # Діагностика
            else:
                 # Повідомлення про помилку показується в export_project_data
                 log.error("Export failed (see previous error logs).") # Діагностика
        except Exception as e:
            log.error(f"Failed to export project: {e}", exc_info=True) # Діагностика
            QMessageBox.critical(self, "Помилка експорту", f"Не вдалося експортувати проект:\n{e}")

    # --- Other UI Actions ---
    def add_comment(self):
        log.debug("Add comment triggered.") # Діагностика
        if self.current_edit_mode == EDIT_MODE_SCENARIO and self.active_scenario_id is None:
            log.warning("  Cannot add comment: No active scenario.") # Діагностика
            return
        if self.current_edit_mode == EDIT_MODE_MACRO and self.active_macro_id is None:
            log.warning("  Cannot add comment: No active macro.") # Діагностика
            return
        center_pos = self.view.mapToScene(self.view.viewport().rect().center())
        log.debug(f"  Adding comment at scene pos ({center_pos.x():.1f}, {center_pos.y():.1f})") # Діагностика
        # --- Імпорт команди ---
        from commands import AddCommentCommand
        command = AddCommentCommand(self.scene, center_pos, self.view)
        self.undo_stack.push(command)

    def show_status_message(self, message, timeout=4000, color=None):
        # Логіка без змін
        style = f"color: {color};" if color else ""
        self.statusBar().setStyleSheet(style)
        self.statusBar().showMessage(message, timeout)
        # Скидання стилю після таймауту
        if color:
             # Використовуємо lambda, щоб передати порожній рядок стилю
             QTimer.singleShot(timeout, lambda: self.statusBar().setStyleSheet(""))

    # --- Close Event ---
    def closeEvent(self, event):
        log.debug("Close event triggered.") # Діагностика
        # TODO: Додати перевірку на незбережені зміни
        # reply = QMessageBox.question(self, 'Вихід',
        #                              "Зберегти зміни перед виходом?",
        #                              QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        # if reply == QMessageBox.StandardButton.Save:
        #     self.export_project() # Потрібно перевірити, чи експорт вдався
        #     event.accept()
        # elif reply == QMessageBox.StandardButton.Discard:
        #     event.accept()
        # else:
        #     event.ignore()
        event.accept() # Поки що просто закриваємо

