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
    QScrollArea, QInputDialog, QSpacerItem, QSizePolicy
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

        self._create_actions()
        self._create_menu_bar()
        self._create_toolbars() # Перейменовано
        self._create_simulation_toolbar()
        self._create_panels() # Цей метод значно спроститься

        self.scene.selectionChanged.connect(self.on_selection_changed)
        # --- ЗАМІНА: Використовуємо _trigger_validation замість прямої валідації ---
        self.undo_stack.indexChanged.connect(self._trigger_validation)
        # --- КІНЕЦЬ ЗАМІНИ ---
        self.undo_stack.indexChanged.connect(self._update_simulation_trigger_zones) # Симуляція залишається тут

        self.new_project() # Викликаємо метод ініціалізації

        self.statusBar().showMessage("Готово")
        self._update_window_title()
        log.debug("MainWindow initialized.")

    def _trigger_validation(self):
        """Запускає валідацію з невеликою затримкою."""
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
        # Видаляємо старі динамічні тулбари
        for toolbar in self.findChildren(QToolBar):
            if toolbar.objectName() in ["scenario_toolbar", "macro_toolbar"]:
                self.removeToolBar(toolbar)
                toolbar.deleteLater()
        # Оновлюємо динамічні
        self._update_node_toolbars()

    def _update_node_toolbars(self):
        # Видаляємо існуючі, якщо є
        if hasattr(self, 'scenario_toolbar'):
             self.removeToolBar(self.scenario_toolbar)
             self.scenario_toolbar.deleteLater()
             del self.scenario_toolbar
        if hasattr(self, 'macro_toolbar'):
             self.removeToolBar(self.macro_toolbar)
             self.macro_toolbar.deleteLater()
             del self.macro_toolbar

        if self.current_edit_mode == EDIT_MODE_SCENARIO:
            self._create_scenario_toolbar()
            self.back_to_scenario_action.setEnabled(False)
        elif self.current_edit_mode == EDIT_MODE_MACRO:
            self._create_macro_toolbar()
            self.back_to_scenario_action.setEnabled(True)

        # Оновлюємо видимість панелі симуляції
        if hasattr(self, 'sim_toolbar'): # Перевіряємо наявність
            self.sim_toolbar.setVisible(self.current_edit_mode == EDIT_MODE_SCENARIO)

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

    def _on_toolbar_action_triggered(self, node_type):
        self.add_node(node_type, self.view.mapToScene(self.view.viewport().rect().center()))

    # --- Panels ---
    def _create_panels(self):
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

        # --- Панель Елементів ---
        nodes_dock = QDockWidget("Елементи сценарію", self)
        self.nodes_list = QListWidget() # Залишаємо віджет списку тут
        self._update_nodes_list() # Початкове заповнення
        nodes_dock.setWidget(self.nodes_list)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, nodes_dock)

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

        # --- Панель Властивостей ---
        props_dock = QDockWidget("Властивості", self)
        self.props_widget = QWidget() # Головний віджет панелі властивостей
        self.main_props_layout = QVBoxLayout(self.props_widget) # Layout для цього віджету
        props_dock.setWidget(self.props_widget)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, props_dock)
        self.setup_properties_panel() # Створення елементів UI властивостей залишається тут
        self._update_properties_panel_ui() # Початкове налаштування видимості
        self.props_widget.setEnabled(False) # Початково вимкнена

        # --- Підключення сигналів ---
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

    def on_project_tab_changed(self, index):
        # Логіка без змін
        if index == 0 and self.current_edit_mode == EDIT_MODE_MACRO:
            self.return_to_scenario()

    def _update_nodes_list(self):
        # Логіка без змін
        self.nodes_list.clear()
        if self.current_edit_mode == EDIT_MODE_SCENARIO:
            items = sorted([name for name in NODE_REGISTRY.keys() if name not in ["Вхід Макроса", "Вихід Макроса"]])
        elif self.current_edit_mode == EDIT_MODE_MACRO:
            items = sorted(["Вхід Макроса", "Вихід Макроса", "Затримка", "Умова: Стан зони", "Повтор"])
        self.nodes_list.addItems(items)

    # --- Properties Panel Setup and Update (залишаються тут) ---
    def setup_properties_panel(self):
        # Цей метод створює віджети (QLineEdit, QComboBox і т.д.) для панелі властивостей
        # Логіка створення віджетів не змінилася
        # Видаляємо старі віджети
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

        # Властивості Тригера
        self.trigger_props_widget = QWidget()
        trigger_layout = QFormLayout(self.trigger_props_widget)
        self.trigger_type_combo = QComboBox()
        self.trigger_type_combo.addItems(["Пожежа", "Тривога", "Несправність 220В", "Зняття з охорони"])
        trigger_layout.addRow("Спосіб запуску:", self.trigger_type_combo)
        self.zones_container = QWidget()
        self.zones_layout = QGridLayout(self.zones_container)
        self.zones_layout.setContentsMargins(0, 0, 0, 0)
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True); scroll_area.setWidget(self.zones_container)
        scroll_area.setMinimumHeight(100)
        trigger_layout.addRow("Зони:", scroll_area)
        self.main_props_layout.addWidget(self.trigger_props_widget)
        self.trigger_type_combo.currentTextChanged.connect(self._schedule_properties_apply) # Підключення сигналу

        # Властивості Виходу
        self.output_props_widget = QWidget()
        output_layout = QFormLayout(self.output_props_widget)
        self.output_select_combo = QComboBox()
        output_layout.addRow("Вихід:", self.output_select_combo)
        self.main_props_layout.addWidget(self.output_props_widget)
        self.output_select_combo.currentIndexChanged.connect(self._schedule_properties_apply) # Підключення сигналу

        # Властивості Затримки
        self.delay_props_widget = QWidget()
        delay_layout = QFormLayout(self.delay_props_widget)
        self.delay_spinbox = QSpinBox()
        self.delay_spinbox.setRange(0, 3600); self.delay_spinbox.setSuffix(" сек.")
        delay_layout.addRow("Час затримки:", self.delay_spinbox)
        self.main_props_layout.addWidget(self.delay_props_widget)
        self.delay_spinbox.valueChanged.connect(self._schedule_properties_apply) # Підключення сигналу

        # Властивості SMS
        self.sms_props_widget = QWidget()
        sms_layout = QFormLayout(self.sms_props_widget)
        self.sms_user_combo = QComboBox()
        self.sms_message_text = QLineEdit()
        sms_layout.addRow("Користувач:", self.sms_user_combo)
        sms_layout.addRow("Повідомлення:", self.sms_message_text)
        self.main_props_layout.addWidget(self.sms_props_widget)
        self.sms_user_combo.currentIndexChanged.connect(self._schedule_properties_apply) # Підключення сигналу
        self.sms_message_text.textChanged.connect(self._schedule_properties_apply) # Підключення сигналу

        # Властивості Умови
        self.condition_props_widget = QWidget()
        condition_layout = QFormLayout(self.condition_props_widget)
        self.condition_zone_combo = QComboBox()
        self.condition_state_combo = QComboBox()
        self.condition_state_combo.addItems(["Під охороною", "Знята з охорони", "Тривога"])
        condition_layout.addRow("Зона:", self.condition_zone_combo)
        condition_layout.addRow("Перевірити стан:", self.condition_state_combo)
        self.main_props_layout.addWidget(self.condition_props_widget)
        self.condition_zone_combo.currentIndexChanged.connect(self._schedule_properties_apply) # Підключення сигналу
        self.condition_state_combo.currentIndexChanged.connect(self._schedule_properties_apply) # Підключення сигналу

        # Властивості Повтору
        self.repeat_props_widget = QWidget()
        repeat_layout = QFormLayout(self.repeat_props_widget)
        self.repeat_count_spinbox = QSpinBox()
        self.repeat_count_spinbox.setRange(-1, 100); self.repeat_count_spinbox.setSpecialValueText("Безкінечно")
        repeat_layout.addRow("Кількість повторів:", self.repeat_count_spinbox)
        self.main_props_layout.addWidget(self.repeat_props_widget)
        self.repeat_count_spinbox.valueChanged.connect(self._schedule_properties_apply) # Підключення сигналу

        # Властивості Макросу
        self.macro_props_widget = QWidget()
        macro_layout = QFormLayout(self.macro_props_widget)
        self.macro_definition_combo = QComboBox()
        macro_layout.addRow("Визначення:", self.macro_definition_combo)
        self.main_props_layout.addWidget(self.macro_props_widget)
        self.macro_definition_combo.currentIndexChanged.connect(self._schedule_properties_apply) # Підключення сигналу

        # Розпірка
        spacer = QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        self.main_props_layout.addSpacerItem(spacer)
        log.debug("Properties panel widgets created.")

    def _schedule_properties_apply(self):
        # Логіка без змін
        if self.props_widget.isEnabled():
            self.props_apply_timer.start()

    def on_selection_changed(self):
        # Логіка без змін, вона працює з self.current_selected_node та self.props_widget
        if self.simulator.is_running: return
        self.props_apply_timer.stop()
        selected_items = self.scene.selectedItems()
        newly_selected_node = None
        if len(selected_items) == 1 and isinstance(selected_items[0], BaseNode):
            newly_selected_node = selected_items[0]

        # Не застосовуємо зміни властивостей, якщо вибір змінився на інший вузол або зник
        if self.current_selected_node and self.current_selected_node != newly_selected_node:
             pass # Можливо, тут варто було б спробувати зберегти, але таймер це робить
             # log.debug(f"Selection changed from {self.current_selected_node.id} to {newly_selected_node.id if newly_selected_node else 'None'}. Stopping potential property apply.")

        self.current_selected_node = newly_selected_node
        self.props_widget.setEnabled(self.current_selected_node is not None)
        self._update_properties_panel_ui()

    def _update_properties_panel_ui(self):
        # Цей метод оновлює значення та видимість віджетів у панелі властивостей
        # Він використовує self.current_selected_node та віджети, створені в setup_properties_panel
        # --- ЗАМІНА: Отримуємо дані конфігурації з менеджера ---
        config_data = self.project_manager.get_config_data()
        all_zones, all_outputs = self.project_manager.get_all_zones_and_outputs()
        macros_data = self.project_manager.get_macros_data()
        # --- КІНЕЦЬ ЗАМІНИ ---

        node = self.current_selected_node if self.props_widget.isEnabled() else None
        is_node_selected = node is not None

        # Сховати всі специфічні панелі
        self.trigger_props_widget.setVisible(False); self.output_props_widget.setVisible(False)
        self.delay_props_widget.setVisible(False); self.sms_props_widget.setVisible(False)
        self.condition_props_widget.setVisible(False); self.repeat_props_widget.setVisible(False)
        self.macro_props_widget.setVisible(False)

        # Показати базову панель, якщо щось вибрано
        self.base_props_widget.setVisible(is_node_selected)
        self.prop_name.setEnabled(is_node_selected)

        if is_node_selected:
            # Заповнити базові поля
            self.prop_name.blockSignals(True); self.prop_description.blockSignals(True)
            self.prop_name.setText(node.node_name)
            self.prop_description.setPlainText(node.description)
            self.prop_name.blockSignals(False); self.prop_description.blockSignals(False)

            # Показати та заповнити специфічну панель
            props = dict(node.properties) # Поточні властивості вузла

            if isinstance(node, TriggerNode):
                self.trigger_props_widget.setVisible(True)
                self.trigger_type_combo.blockSignals(True)
                self.trigger_type_combo.setCurrentText(props.get('trigger_type', 'Пожежа'))
                self.trigger_type_combo.blockSignals(False)
                # Очистка та заповнення чекбоксів зон
                while self.zones_layout.count():
                    child = self.zones_layout.takeAt(0)
                    if child.widget(): child.widget().deleteLater()
                selected_zones = props.get('zones', [])
                for i, zone in enumerate(all_zones):
                    checkbox = QCheckBox(f"{zone['parent_name']}: {zone['name']}")
                    checkbox.setChecked(zone['id'] in selected_zones)
                    checkbox.toggled.connect(self._schedule_properties_apply) # Сигнал підключено
                    checkbox.setProperty("zone_id", zone['id'])
                    self.zones_layout.addWidget(checkbox, i // 2, i % 2)

            elif isinstance(node, (ActivateOutputNode, DeactivateOutputNode)):
                self.output_props_widget.setVisible(True)
                self.output_select_combo.blockSignals(True)
                self.output_select_combo.clear()
                self.output_select_combo.addItem("Не призначено", userData=None)
                for output in all_outputs:
                     self.output_select_combo.addItem(f"{output['parent_name']}: {output['name']}", userData=output['id'])
                selected_id = props.get('output_id')
                index = self.output_select_combo.findData(selected_id) if selected_id else 0
                self.output_select_combo.setCurrentIndex(max(0, index))
                self.output_select_combo.blockSignals(False)

            elif isinstance(node, DelayNode):
                self.delay_props_widget.setVisible(True)
                self.delay_spinbox.blockSignals(True)
                self.delay_spinbox.setValue(int(props.get('seconds', 0)))
                self.delay_spinbox.blockSignals(False)

            elif isinstance(node, SendSMSNode):
                self.sms_props_widget.setVisible(True)
                self.sms_user_combo.blockSignals(True)
                self.sms_user_combo.clear()
                self.sms_user_combo.addItem("Не призначено", userData=None)
                # --- ВИКОРИСТАННЯ config_data ---
                for user in config_data.get('users', []):
                     self.sms_user_combo.addItem(user['name'], userData=user['id'])
                selected_id = props.get('user_id')
                index = self.sms_user_combo.findData(selected_id) if selected_id else 0
                self.sms_user_combo.setCurrentIndex(max(0, index))
                self.sms_user_combo.blockSignals(False)
                self.sms_message_text.blockSignals(True)
                self.sms_message_text.setText(props.get('message', ''))
                self.sms_message_text.blockSignals(False)

            elif isinstance(node, ConditionNodeZoneState):
                self.condition_props_widget.setVisible(True)
                self.condition_zone_combo.blockSignals(True)
                self.condition_zone_combo.clear()
                self.condition_zone_combo.addItem("Не призначено", userData=None)
                for zone in all_zones:
                     self.condition_zone_combo.addItem(f"{zone['parent_name']}: {zone['name']}", userData=zone['id'])
                selected_id = props.get('zone_id')
                index = self.condition_zone_combo.findData(selected_id) if selected_id else 0
                self.condition_zone_combo.setCurrentIndex(max(0, index))
                self.condition_zone_combo.blockSignals(False)
                self.condition_state_combo.blockSignals(True)
                self.condition_state_combo.setCurrentText(props.get('state', 'Під охороною'))
                self.condition_state_combo.blockSignals(False)

            elif isinstance(node, RepeatNode):
                self.repeat_props_widget.setVisible(True)
                self.repeat_count_spinbox.blockSignals(True)
                self.repeat_count_spinbox.setValue(int(props.get('count', 3)))
                self.repeat_count_spinbox.blockSignals(False)

            elif isinstance(node, MacroNode):
                self.macro_props_widget.setVisible(True)
                self.macro_definition_combo.blockSignals(True)
                self.macro_definition_combo.clear()
                self.macro_definition_combo.addItem("Не призначено", userData=None)
                # --- ВИКОРИСТАННЯ macros_data ---
                for macro_id, macro_data in macros_data.items():
                    self.macro_definition_combo.addItem(macro_data.get('name', macro_id), userData=macro_id)
                selected_id = node.macro_id # Властивість MacroNode
                index = self.macro_definition_combo.findData(selected_id) if selected_id else 0
                self.macro_definition_combo.setCurrentIndex(max(0, index))
                self.macro_definition_combo.blockSignals(False)

        else: # Якщо нічого не вибрано
            self.prop_name.clear()
            self.prop_description.clear()

    # --- Збереження властивостей (залишається тут) ---
    def on_apply_button_clicked(self):
        # Цей метод читає значення з віджетів панелі властивостей
        # і створює команду ChangePropertiesCommand
        if not self.current_selected_node: return
        node = self.current_selected_node
        log.debug(f"Applying properties for node {node.id}")

        # Зберігаємо старі дані для undo
        old_name = node.node_name
        old_desc = node.description
        old_props = list(node.properties) # Копія списку
        old_macro_id = getattr(node, 'macro_id', None)
        old_data = {'name': old_name, 'desc': old_desc, 'props': old_props, 'macro_id': old_macro_id}

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

             # Створюємо команду, тільки якщо дані змінились
             if old_data != new_data:
                 log.debug(f"  Properties changed. Old: {old_data}, New: {new_data}")
                 # --- Оновлення імені у визначенні макросу ---
                 if self.current_edit_mode == EDIT_MODE_MACRO and self.active_macro_id \
                    and isinstance(node, (MacroInputNode, MacroOutputNode)):
                     io_type = 'input' if isinstance(node, MacroInputNode) else 'output'
                     # --- ВИКОРИСТАННЯ project_manager ---
                     self.project_manager.update_macro_io_name(self.active_macro_id, node.id, io_type, new_name)
                     # Оновлюємо сокети MacroNode в сценаріях після зміни імені IO
                     self.update_macro_nodes_in_scenarios(self.active_macro_id)
                     # --- КІНЕЦЬ ---

                 # --- Імпорт команди ---
                 from commands import ChangePropertiesCommand
                 command = ChangePropertiesCommand(node, old_data, new_data)
                 self.undo_stack.push(command)
             else:
                 log.debug("  Properties not changed.")

        except Exception as e:
             log.error(f"Error reading properties from UI for node {node.id}: {e}", exc_info=True)
             QMessageBox.warning(self, "Помилка", f"Не вдалося зчитати властивості: {e}")


    # --- Configuration Panel Actions ---
    def update_config_ui(self):
        """Оновлює таблиці конфігурації даними з ProjectManager."""
        log.debug("Updating configuration UI panels...")
        # --- ВИКОРИСТАННЯ project_manager ---
        config = self.project_manager.get_config_data()
        all_zones, all_outputs = self.project_manager.get_all_zones_and_outputs()
        # --- КІНЕЦЬ ---

        # Блокуємо сигнали таблиць
        self.devices_table.blockSignals(True); self.zones_table.blockSignals(True)
        self.outputs_table.blockSignals(True); self.users_table.blockSignals(True)

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

        # Розблоковуємо сигнали
        self.devices_table.blockSignals(False); self.zones_table.blockSignals(False)
        self.outputs_table.blockSignals(False); self.users_table.blockSignals(False)

        # --- ЗАМІНА: Викликаємо валідацію та оновлення властивостей ---
        self._trigger_validation() # Викликаємо валідацію після оновлення UI
        if self.current_selected_node: self.on_selection_changed() # Оновлюємо панель властивостей
        # --- КІНЕЦЬ ЗАМІНИ ---
        log.debug("Configuration UI panels updated.")


    def add_device(self):
        """Обробник кнопки додавання пристрою."""
        device_type = self.device_type_combo.currentText()
        # --- ВИКОРИСТАННЯ project_manager ---
        new_id = self.project_manager.add_device(device_type)
        if new_id:
            self.update_config_ui() # Оновлюємо UI після зміни даних
        # --- КІНЕЦЬ ---

    def remove_device(self):
        """Обробник кнопки видалення пристрою."""
        selected_rows = sorted(list(set(index.row() for index in self.devices_table.selectedIndexes())), reverse=True)
        if not selected_rows: return

        ids_to_remove = []
        names_to_remove = []
        for row in selected_rows:
            id_item = self.devices_table.item(row, 0)
            name_item = self.devices_table.item(row, 1)
            if id_item:
                ids_to_remove.append(id_item.text())
                names_to_remove.append(name_item.text() if name_item else id_item.text())

        if not ids_to_remove: return

        reply = QMessageBox.question(self, "Підтвердження видалення",
                                     f"Ви впевнені, що хочете видалити пристрої:\n - {', '.join(names_to_remove)}?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            removed_count = 0
            for device_id in ids_to_remove:
                # --- ВИКОРИСТАННЯ project_manager ---
                if self.project_manager.remove_device(device_id):
                    removed_count += 1
                # --- КІНЕЦЬ ---
            if removed_count > 0:
                self.update_config_ui() # Оновлюємо UI

    def add_config_item(self, config_key):
        """Обробник кнопки додавання користувача."""
        if config_key != 'users': return
        # --- ВИКОРИСТАННЯ project_manager ---
        new_id = self.project_manager.add_user()
        if new_id:
            self.update_config_ui()
        # --- КІНЕЦЬ ---

    def remove_config_item(self, config_key):
        """Обробник кнопки видалення користувача."""
        if config_key != 'users': return
        row = self.users_table.currentRow()
        if row > -1:
            id_item = self.users_table.item(row, 0)
            if id_item:
                user_id = id_item.text()
                # --- ВИКОРИСТАННЯ project_manager ---
                if self.project_manager.remove_user(user_id):
                    self.update_config_ui()
                # --- КІНЕЦЬ ---

    def on_config_table_changed(self, item):
        """Обробник зміни даних в таблицях конфігурації."""
        table = item.tableWidget()
        row, col = item.row(), item.column()
        id_item = table.item(row, 0)
        if not id_item: return
        item_id = id_item.text()
        new_value = item.text()

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
            # --- ВИКОРИСТАННЯ project_manager ---
            updated = self.project_manager.update_config_item(item_type, item_id, data_key, new_value)
            # --- КІНЕЦЬ ---
            if updated:
                 # Викликаємо оновлення UI один раз після всіх можливих змін
                 QTimer.singleShot(0, self.update_config_ui)
        else:
             log.warning(f"Unhandled config table change: Table={table}, Row={row}, Col={col}")


    # --- Node Actions ---
    def add_node(self, node_type, position):
        # Логіка перевірок (чи вибрано сценарій/макрос, чи можна додати тип) залишається тут
        if self.current_edit_mode == EDIT_MODE_SCENARIO and self.active_scenario_id is None:
            self.show_status_message("Спочатку виберіть або створіть сценарій.", 5000, color="orange")
            return
        if self.current_edit_mode == EDIT_MODE_MACRO and self.active_macro_id is None:
            self.show_status_message("Неможливо додати вузол: не вибрано макрос для редагування.", 5000, color="red")
            return
        if self.current_edit_mode == EDIT_MODE_MACRO and node_type == "Тригер":
            self.show_status_message("Помилка: Тригер не можна додавати всередині макросу.", 5000, color="red")
            return
        if self.current_edit_mode == EDIT_MODE_MACRO and node_type == "Макрос":
            self.show_status_message("Помилка: Макрос не можна додавати всередині іншого макросу.", 5000, color="red")
            return
        if node_type == "Тригер" and any(isinstance(i, TriggerNode) for i in self.scene.items()):
            self.show_status_message("Помилка: Тригер у сценарії може бути лише один.", 5000, color="red")
            return

        # Створення команди
        if node_type in NODE_REGISTRY:
            # --- Імпорт команди ---
            from commands import AddNodeCommand
            command = AddNodeCommand(self.scene, node_type, position)
            self.undo_stack.push(command)

    def on_node_list_clicked(self, item):
        self.add_node(item.text(), self.view.mapToScene(self.view.viewport().rect().center()))

    # --- Project Management (delegation) ---
    def new_project(self):
        log.info("MainWindow: new_project called.")
        # --- ВИКОРИСТАННЯ project_manager ---
        self.project_manager.new_project()
        # --- КІНЕЦЬ ---
        self.scene.clear()
        self.undo_stack.clear()
        self.set_edit_mode(EDIT_MODE_SCENARIO) # Перемикаємо режим
        # --- ЗАМІНА: Отримуємо ID першого сценарію з менеджера ---
        first_scenario_id = self.project_manager.get_first_scenario_id()
        if first_scenario_id:
            self.active_scenario_id = first_scenario_id
            self.load_scenario_state(self.active_scenario_id) # Завантажуємо його стан
        else:
             self.active_scenario_id = None # На випадок, якщо менеджер не створив сценарій
        # --- КІНЕЦЬ ЗАМІНИ ---
        self.current_selected_node = None # Скидаємо вибір
        self.active_macro_id = None
        self.previous_scenario_id = None
        self.update_ui_from_project() # Оновлюємо UI на основі даних менеджера
        self.props_widget.setEnabled(False)
        self._update_window_title() # Оновлюємо заголовок
        log.debug("MainWindow: new_project finished.")


    def add_scenario(self):
        # --- ВИКОРИСТАННЯ project_manager ---
        new_name = self.project_manager.add_scenario()
        # --- КІНЕЦЬ ---
        if new_name:
            self.update_scenarios_list() # Оновлюємо список
            items = self.scenarios_list.findItems(new_name, Qt.MatchFlag.MatchExactly)
            if items:
                self.scenarios_list.setCurrentItem(items[0]) # Автоматично вибираємо новий

    def remove_scenario(self):
        current_item = self.scenarios_list.currentItem()
        if not current_item: return
        scenario_name = current_item.text()
        # --- ВИКОРИСТАННЯ project_manager ---
        if len(self.project_manager.get_scenario_ids()) <= 1:
            QMessageBox.warning(self, "Неможливо видалити", "Неможливо видалити останній сценарій.")
            return
        # --- КІНЕЦЬ ---

        reply = QMessageBox.question(self, "Видалення Сценарію",
                                     f"Ви впевнені, що хочете видалити сценарій '{scenario_name}'?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.No: return

        # --- ВИКОРИСТАННЯ project_manager ---
        removed = self.project_manager.remove_scenario(scenario_name)
        # --- КІНЕЦЬ ---
        if removed:
            self.active_scenario_id = None # Скидаємо активний
            self.update_scenarios_list() # Оновлюємо список
            # Вибираємо перший сценарій, якщо він є
            first_id = self.project_manager.get_first_scenario_id()
            if first_id:
                items = self.scenarios_list.findItems(first_id, Qt.MatchFlag.MatchExactly)
                if items:
                    self.scenarios_list.setCurrentItem(items[0]) # Це викличе on_active_scenario_changed
            else: # Якщо сценаріїв не залишилось
                self.scene.clear()
                self._update_window_title()
            self.undo_stack.clear() # Очищаємо історію

    def remove_macro(self):
        current_item = self.macros_list.currentItem()
        if not current_item: return
        macro_name = current_item.text()
        macro_id = current_item.data(Qt.ItemDataRole.UserRole)
        if not macro_id: return

        # --- ВИКОРИСТАННЯ project_manager ---
        usage_count, usage_scenarios = self.project_manager.check_macro_usage(macro_id)
        if usage_count > 0:
            QMessageBox.warning(self, "Неможливо видалити макрос",
                                f"Макрос '{macro_name}' використовується у {usage_count} вузлах сценаріїв:\n"
                                f"{', '.join(usage_scenarios)}\nСпочатку видаліть ці вузли.")
            return
        # --- КІНЕЦЬ ---

        reply = QMessageBox.question(self, "Видалення Макросу",
                                     f"Ви впевнені, що хочете видалити макрос '{macro_name}'?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.No: return

        # --- ВИКОРИСТАННЯ project_manager ---
        removed = self.project_manager.remove_macro(macro_id)
        # --- КІНЕЦЬ ---
        if removed:
            if self.active_macro_id == macro_id:
                self.return_to_scenario(force_return=True) # Повертаємось, якщо видалили поточний
            self.update_macros_list() # Оновлюємо список
            # Потенційно оновити MacroNode, якщо вони стали невалідними? Валідація це зробить.

    def rename_macro(self):
        current_item = self.macros_list.currentItem()
        if not current_item: return
        macro_id = current_item.data(Qt.ItemDataRole.UserRole)
        if not macro_id: return

        # --- ВИКОРИСТАННЯ project_manager ---
        macro_data = self.project_manager.get_macro_data(macro_id)
        current_name = macro_data.get('name', macro_id) if macro_data else macro_id
        # --- КІНЕЦЬ ---

        new_name, ok = QInputDialog.getText(self, "Перейменувати Макрос",
                                            "Нове ім'я макросу:", QLineEdit.EchoMode.Normal, current_name)

        if ok and new_name.strip():
            # --- ВИКОРИСТАННЯ project_manager ---
            success = self.project_manager.rename_macro(macro_id, new_name.strip())
            # --- КІНЕЦЬ ---
            if success:
                self.update_macros_list()
                self._update_all_items_properties() # Оновити MacroNode на сцені
                if self.active_macro_id == macro_id: self._update_window_title() # Оновити заголовок
            else:
                 QMessageBox.warning(self, "Помилка", "Не вдалося перейменувати макрос (можливо, ім'я вже існує).")


    # --- Обробники подій списків ---
    def on_scenario_item_double_clicked(self, item):
        # Логіка без змін
        if self.current_edit_mode == EDIT_MODE_MACRO:
            if not self.return_to_scenario(): return
        self._old_scenario_name = item.text()
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        self.scenarios_list.editItem(item)

    def on_macro_item_double_clicked(self, item):
        # Логіка без змін
        macro_id = item.data(Qt.ItemDataRole.UserRole)
        if macro_id:
            self.edit_macro(macro_id)

    def on_scenario_renamed(self, item):
        # Цей метод обробляє ЗАВЕРШЕННЯ редагування в QListWidget
        new_name = item.text().strip()
        old_name = self._old_scenario_name
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable) # Знімаємо флаг

        if not new_name or not old_name or new_name == old_name:
            if old_name: item.setText(old_name) # Повертаємо старе ім'я
            self._old_scenario_name = None
            return

        # --- ВИКОРИСТАННЯ project_manager ---
        success = self.project_manager.rename_scenario(old_name, new_name)
        # --- КІНЕЦЬ ---

        if not success:
            QMessageBox.warning(self, "Помилка перейменування", f"Не вдалося перейменувати сценарій '{old_name}' (можливо, ім'я '{new_name}' вже існує).")
            item.setText(old_name) # Повертаємо старе ім'я в UI
        else:
            if self.active_scenario_id == old_name:
                self.active_scenario_id = new_name # Оновлюємо активний ID
                self._update_window_title() # Оновлюємо заголовок вікна

        self._old_scenario_name = None # Скидаємо збережене старе ім'я

    def on_active_scenario_changed(self, current_item, previous_item):
        # Логіка зміни сценарію (збереження попереднього, завантаження поточного)
        # --- МАЙЖЕ БЕЗ ЗМІН, але використовує save_current_state/load_scenario_state ---
        log.debug(f"MW: on_active_scenario_changed. Current: {current_item.text() if current_item else 'None'}")
        if self.current_edit_mode == EDIT_MODE_MACRO:
             log.debug("  Ignoring scenario change in macro mode.")
             # Відновлення вибору в списку, якщо потрібно
             if self.previous_scenario_id:
                 items = self.scenarios_list.findItems(self.previous_scenario_id, Qt.MatchFlag.MatchExactly)
                 if items:
                      self.scenarios_list.blockSignals(True)
                      self.scenarios_list.setCurrentItem(items[0])
                      self.scenarios_list.blockSignals(False)
             return

        if self.simulator.is_running: self.stop_simulation()

        # Зберігаємо попередній стан (якщо він був і це був сценарій)
        if previous_item:
            prev_id = previous_item.text()
            # Перевіряємо, чи існує сценарій з таким ID в менеджері
            if self.project_manager.get_scenario_data(prev_id):
                 log.debug(f"  Saving state for previous scenario: {prev_id}")
                 self.save_current_state() # Збереже поточний активний сценарій

        # Завантажуємо новий стан
        if current_item:
            new_active_id = current_item.text()
            log.debug(f"  Loading state for new scenario: {new_active_id}")
            # Не встановлюємо active_scenario_id тут, це зробить load_scenario_state
            self.load_scenario_state(new_active_id) # Завантажить дані та оновить self.active_scenario_id
        else:
            log.debug("  No current item selected, clearing scene.")
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
        log.info(f"Switching edit mode to {'MACRO' if mode == EDIT_MODE_MACRO else 'SCENARIO'}")
        self.current_edit_mode = mode
        self.undo_stack.clear()
        self._update_node_toolbars() # Оновлюємо ТІЛЬКИ динамічні тулбари
        self._update_window_title()
        self._update_nodes_list()
        self.sim_toolbar.setVisible(mode == EDIT_MODE_SCENARIO)
        self.scene.clearSelection()
        self.current_selected_node = None
        self.on_selection_changed()

    def edit_macro(self, macro_id):
        # --- ВИКОРИСТАННЯ project_manager ---
        if not self.project_manager.get_macro_data(macro_id):
            log.error(f"Cannot edit macro: ID {macro_id} not found.")
            return
        # --- КІНЕЦЬ ---

        if self.current_edit_mode == EDIT_MODE_MACRO and self.active_macro_id == macro_id: return

        self.save_current_state() # Зберігаємо поточний стан (сценарію)
        self.previous_scenario_id = self.active_scenario_id # Запам'ятовуємо сценарій

        # Перемикаємо режим та завантажуємо стан макросу
        self.set_edit_mode(EDIT_MODE_MACRO)
        self.load_macro_state(macro_id) # Це оновить active_macro_id та заголовок

        # Оновлення UI списків
        self.project_tabs.setCurrentIndex(1) # Перемкнути на вкладку макросів
        items = self.macros_list.findItems(self.project_manager.get_macro_data(macro_id)['name'], Qt.MatchFlag.MatchExactly)
        if items:
            self.macros_list.blockSignals(True)
            self.macros_list.setCurrentItem(items[0])
            self.macros_list.blockSignals(False)

    def return_to_scenario(self, force_return=False):
        # --- МАЙЖЕ БЕЗ ЗМІН, але використовує save_current_state/load_scenario_state ---
        if self.current_edit_mode != EDIT_MODE_MACRO: return True

        self.save_current_state() # Зберігаємо стан макросу

        scenario_to_load = self.previous_scenario_id
        self.previous_scenario_id = None # Скидаємо
        # active_macro_id скинеться в load_scenario_state
        self.set_edit_mode(EDIT_MODE_SCENARIO) # Перемикаємо режим

        # Завантажуємо попередній сценарій або перший доступний
        scenario_found_to_load = False
        if scenario_to_load and self.project_manager.get_scenario_data(scenario_to_load):
            self.load_scenario_state(scenario_to_load)
            scenario_found_to_load = True
        else:
            first_id = self.project_manager.get_first_scenario_id()
            if first_id:
                self.load_scenario_state(first_id)
                scenario_found_to_load = True
            else: # Якщо сценаріїв взагалі немає
                self.scene.clear()
                self.active_scenario_id = None
                self._update_window_title()

        # Оновлення UI списків
        self.project_tabs.setCurrentIndex(0) # Перемкнути на вкладку сценаріїв
        if scenario_found_to_load and self.active_scenario_id:
            items = self.scenarios_list.findItems(self.active_scenario_id, Qt.MatchFlag.MatchExactly)
            if items:
                self.scenarios_list.blockSignals(True)
                self.scenarios_list.setCurrentItem(items[0])
                self.scenarios_list.blockSignals(False)
        return True

    # --- Scene State Save/Load ---
    def save_current_state(self):
        """Зберігає поточний стан сцени в ProjectManager."""
        log.debug("MW: save_current_state called.")
        # --- ВИКОРИСТАННЯ scene_utils ---
        scene_data = extract_data_from_scene(self.scene)
        # --- КІНЕЦЬ ---
        if self.current_edit_mode == EDIT_MODE_SCENARIO and self.active_scenario_id:
            # --- ВИКОРИСТАННЯ project_manager ---
            self.project_manager.update_scenario_data(self.active_scenario_id, scene_data)
            # --- КІНЕЦЬ ---
        elif self.current_edit_mode == EDIT_MODE_MACRO and self.active_macro_id:
            # --- ВИКОРИСТАННЯ project_manager ---
            updated_macro_data = self.project_manager.update_macro_data(self.active_macro_id, scene_data)
            # --- КІНЕЦЬ ---
            if updated_macro_data: # Якщо змінились входи/виходи макросу
                 log.debug("Macro definition changed, updating MacroNodes in scenarios...")
                 self.update_macro_nodes_in_scenarios(self.active_macro_id)
        else:
            log.warning("save_current_state: No active scenario or macro to save.")

    def load_scenario_state(self, scenario_id):
        """Завантажує стан сценарію зі ProjectManager на сцену."""
        log.debug(f"MW: load_scenario_state for {scenario_id}")
        # --- ВИКОРИСТАННЯ project_manager ---
        scenario_data = self.project_manager.get_scenario_data(scenario_id)
        macros_data = self.project_manager.get_macros_data()
        # --- КІНЕЦЬ ---
        if scenario_data:
            self.scene.clear()
            # --- ВИКОРИСТАННЯ scene_utils ---
            populate_scene_from_data(self.scene, scenario_data, self.view, macros_data)
            # --- КІНЕЦЬ ---
            self.active_scenario_id = scenario_id
            self.active_macro_id = None # Ми в режимі сценарію
            self._update_window_title()
            self._trigger_validation() # Запускаємо валідацію
            self._update_simulation_trigger_zones() # Оновлюємо симуляцію
            log.debug(f"Scenario {scenario_id} loaded successfully.")
        else:
            log.error(f"MW: Scenario data not found for {scenario_id}")
            self.scene.clear() # Очищаємо сцену, якщо дані не знайдено
            self.active_scenario_id = None
            self._update_window_title()

    def load_macro_state(self, macro_id):
        """Завантажує стан макросу зі ProjectManager на сцену."""
        log.debug(f"MW: load_macro_state for {macro_id}")
        # --- ВИКОРИСТАННЯ project_manager ---
        macro_data = self.project_manager.get_macro_data(macro_id)
        # --- КІНЕЦЬ ---
        if macro_data:
            self.scene.clear()
            # --- ВИКОРИСТАННЯ scene_utils ---
            populate_scene_from_data(self.scene, macro_data, self.view) # Не передаємо макроси всередину
            # --- КІНЕЦЬ ---
            self.active_scenario_id = None # Ми в режимі макросу
            self.active_macro_id = macro_id
            self._update_window_title()
            self._trigger_validation() # Запускаємо валідацію
            log.debug(f"Macro {macro_id} loaded successfully.")
        else:
            log.error(f"MW: Macro data not found for {macro_id}")
            self.scene.clear()
            self.active_macro_id = None
            self._update_window_title()

    def update_macro_nodes_in_scenarios(self, updated_macro_id):
        """Оновлює MacroNode на поточній сцені, якщо вона є сценарієм."""
        log.debug(f"MW: Updating MacroNodes for macro {updated_macro_id}")
        if self.current_edit_mode != EDIT_MODE_SCENARIO:
             log.debug("  Skipping update, not in scenario mode.")
             return # Оновлюємо тільки якщо зараз відкрито сценарій

        # --- ВИКОРИСТАННЯ project_manager ---
        macro_data = self.project_manager.get_macro_data(updated_macro_id)
        # --- КІНЕЦЬ ---
        if not macro_data:
             log.warning(f"  Cannot update MacroNodes, definition for {updated_macro_id} not found.")
             return

        needs_validation = False
        for item in self.scene.items():
            if isinstance(item, MacroNode) and item.macro_id == updated_macro_id:
                log.debug(f"  Updating sockets for MacroNode {item.id} on current scene.")
                item.update_sockets_from_definition(macro_data)
                # TODO: Перевірити/видалити невалідні з'єднання після оновлення сокетів
                needs_validation = True

        if needs_validation:
             self._trigger_validation() # Запускаємо валідацію, якщо були зміни

    # --- UI Updates ---
    def update_ui_from_project(self):
        """Оновлює списки сценаріїв, макросів та таблиці конфігурації."""
        log.debug("MW: Updating UI from project data...")
        self.update_scenarios_list()
        self.update_macros_list()
        self.update_config_ui() # Цей метод тепер просто відображає дані менеджера
        self._update_nodes_list()
        # Валідація буде викликана з update_config_ui
        log.debug("MW: UI update finished.")

    def update_scenarios_list(self):
        """Оновлює список сценаріїв у UI."""
        log.debug("Updating scenarios list widget...")
        current_text = self.scenarios_list.currentItem().text() if self.scenarios_list.currentItem() else self.active_scenario_id
        self.scenarios_list.blockSignals(True)
        self.scenarios_list.clear()
        # --- ВИКОРИСТАННЯ project_manager ---
        scenario_ids = self.project_manager.get_scenario_ids()
        # --- КІНЕЦЬ ---
        for name in scenario_ids:
            item = QListWidgetItem(name)
            self.scenarios_list.addItem(item)
        selected_item = None
        if current_text:
            items = self.scenarios_list.findItems(current_text, Qt.MatchFlag.MatchExactly)
            if items: selected_item = items[0]
        # Якщо активний сценарій не знайдено, вибираємо перший
        if not selected_item and scenario_ids:
             first_id = scenario_ids[0]
             items = self.scenarios_list.findItems(first_id, Qt.MatchFlag.MatchExactly)
             if items: selected_item = items[0]

        if selected_item:
             self.scenarios_list.setCurrentItem(selected_item)
        self.scenarios_list.blockSignals(False)
        log.debug("Scenarios list widget updated.")

    def update_macros_list(self):
        """Оновлює список макросів у UI."""
        log.debug("Updating macros list widget...")
        current_id_selected = None
        current_item = self.macros_list.currentItem()
        if current_item:
            current_id_selected = current_item.data(Qt.ItemDataRole.UserRole)

        self.macros_list.blockSignals(True)
        self.macros_list.clear()
        # --- ВИКОРИСТАННЯ project_manager ---
        macros_data = self.project_manager.get_macros_data()
        # --- КІНЕЦЬ ---
        new_item_to_select = None
        # Сортуємо за іменем
        sorted_macros = sorted(macros_data.items(), key=lambda item: item[1].get('name', item[0]))

        for macro_id, macro_info in sorted_macros:
            item = QListWidgetItem(macro_info.get('name', macro_id))
            item.setData(Qt.ItemDataRole.UserRole, macro_id)
            self.macros_list.addItem(item)
            if macro_id == current_id_selected:
                new_item_to_select = item

        if new_item_to_select:
            self.macros_list.setCurrentItem(new_item_to_select)
        elif self.macros_list.count() > 0 and self.current_edit_mode == EDIT_MODE_MACRO:
             # Якщо редагували макрос, який видалили, вибираємо перший у списку
             pass # Не вибираємо нічого, щоб не ініціювати завантаження іншого макросу

        self.macros_list.blockSignals(False)

        # Оновлюємо комбо-бокс у властивостях, якщо вибрано MacroNode
        if self.current_selected_node and isinstance(self.current_selected_node, MacroNode):
            self._update_properties_panel_ui() # Перезаповнить комбо-бокс
        log.debug("Macros list widget updated.")

    def _update_all_items_properties(self):
        """Оновлює відображення властивостей для всіх вузлів на сцені."""
        log.debug("MW: Updating display properties for all nodes on scene.")
        config_data = self.project_manager.get_config_data()
        for item in self.scene.items():
            if isinstance(item, BaseNode):
                item.update_display_properties(config_data)
        self._trigger_validation() # Викликаємо валідацію після оновлення

    # --- Validation ---
    def validate_current_view(self):
        """Викликає відповідну функцію валідації."""
        log.debug(f"MW: Validating current view (Mode: {self.current_edit_mode}).")
        config_data = self.project_manager.get_config_data() # Отримуємо актуальну конфігурацію
        if self.current_edit_mode == EDIT_MODE_SCENARIO:
            # --- ВИКОРИСТАННЯ validation.py ---
            validate_scenario_on_scene(self.scene, config_data)
            # --- КІНЕЦЬ ---
        elif self.current_edit_mode == EDIT_MODE_MACRO:
            # --- ВИКОРИСТАННЯ validation.py ---
            validate_macro_on_scene(self.scene, config_data)
            # --- КІНЕЦЬ ---

    # --- Simulation ---
    def _update_simulation_trigger_zones(self):
        # Логіка не змінилася, але використовує project_manager
        if self.current_edit_mode != EDIT_MODE_SCENARIO:
            self.sim_trigger_zone_combo.clear()
            self.sim_trigger_zone_combo.addItem("Недоступно в режимі макросу", userData=None)
            self.update_simulation_controls(); return

        self.sim_trigger_zone_combo.clear()
        trigger_node = next((item for item in self.scene.items() if isinstance(item, TriggerNode)), None)

        if not trigger_node:
            self.sim_trigger_zone_combo.addItem("Тригер не знайдено", userData=None)
            self.update_simulation_controls(); return

        props = dict(trigger_node.properties)
        zone_ids = props.get('zones', [])

        if not zone_ids:
            self.sim_trigger_zone_combo.addItem("Немає зон в тригері", userData=None)
        else:
            # --- ВИКОРИСТАННЯ project_manager ---
            all_zones, _ = self.project_manager.get_all_zones_and_outputs()
            # --- КІНЕЦЬ ---
            found_zones = False
            for zid in zone_ids:
                for z in all_zones:
                    if z['id'] == zid:
                        self.sim_trigger_zone_combo.addItem(f"{z.get('parent_name', '?')}: {z['name']}", userData=zid)
                        found_zones = True; break
            if not found_zones:
                self.sim_trigger_zone_combo.addItem("Призначені зони не знайдено", userData=None)

        self.update_simulation_controls()

    def update_simulation_controls(self):
        # Логіка без змін
        sim_enabled = self.current_edit_mode == EDIT_MODE_SCENARIO
        is_ready_for_sim = False
        if sim_enabled and self.scene:
            trigger_node = next((item for item in self.scene.items() if isinstance(item, TriggerNode)), None)
            if trigger_node and not trigger_node.error_icon.isVisible():
                is_ready_for_sim = self.sim_trigger_zone_combo.count() > 0 and self.sim_trigger_zone_combo.currentData() is not None
        is_running = self.simulator.is_running
        self.start_sim_action.setEnabled(sim_enabled and is_ready_for_sim and not is_running)
        self.step_sim_action.setEnabled(sim_enabled and is_running)
        self.stop_sim_action.setEnabled(sim_enabled and is_running)
        self.sim_trigger_zone_combo.setEnabled(sim_enabled and not is_running)

    def start_simulation(self):
        # Логіка без змін
        if self.current_edit_mode != EDIT_MODE_SCENARIO: return
        self.validate_current_view() # Перевіряємо помилки перед запуском
        QApplication.processEvents() # Обробляємо події, щоб валідація завершилась
        for item in self.scene.items():
            if isinstance(item, BaseNode) and item.error_icon.isVisible():
                self.show_status_message("Помилка: Неможливо почати симуляцію, у сценарії є помилки.", 5000, color="red")
                return
        trigger_zone_id = self.sim_trigger_zone_combo.currentData()
        if self.simulator.start(trigger_zone_id):
            self.view.set_interactive(False)
            self.update_simulation_controls()

    def step_simulation(self):
        # Логіка без змін
        if self.current_edit_mode != EDIT_MODE_SCENARIO: return
        self.simulator.step()
        if not self.simulator.is_running: # Симулятор сам зупинився
            # self.show_status_message("Симуляція завершена.", color="lime") # Повідомлення вже є
            self.stop_simulation() # Оновлюємо UI
        else:
            self.update_simulation_controls()

    def stop_simulation(self):
        # Логіка без змін
        self.simulator.stop()
        self.view.set_interactive(True)
        self.update_simulation_controls()

    def get_user_choice_for_condition(self, node):
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
        items = ["Під охороною", "Знята з охорони", "Тривога"]
        item, ok = QInputDialog.getItem(self, "Симуляція: Вузол 'Умова'",
                                        f"Який поточний стан зони {zone_name}?",
                                        items, 0, False)
        return item if ok and item else None

    # --- Clipboard ---
    def copy_selection(self):
        # --- ВИКОРИСТАННЯ clipboard.py ---
        copied_count = copy_selection_to_clipboard(self.scene)
        if copied_count > 0:
            self.show_status_message(f"Скопійовано {copied_count} елемент(и).")
        # --- КІНЕЦЬ ---

    def paste_at_center(self):
        self.paste_selection()

    def paste_selection(self, view_pos=None):
        # --- ВИКОРИСТАННЯ clipboard.py ---
        paste_pos = self.view.mapToScene(view_pos or self.view.viewport().rect().center())
        success = paste_selection_from_clipboard(
            self.scene, paste_pos, self.view, self.current_edit_mode, self.undo_stack
        )
        if not success:
             self.show_status_message("Помилка вставки з буферу обміну.", 5000, color="red")
        # --- КІНЕЦЬ ---

    # --- Import / Export ---
    def import_project(self):
        path, _ = QFileDialog.getOpenFileName(self, "Імпорт проекту", "", "XML Files (*.xml)")
        if not path: return
        log.info(f"Starting project import from: {path}")
        try:
            # --- ВИКОРИСТАННЯ serialization.py ---
            new_project_data = import_project_data(path)
            # --- КІНЕЦЬ ---
            if new_project_data is None:
                log.error("Failed to load project data.")
                QMessageBox.critical(self, "Помилка імпорту", "Не вдалося завантажити дані з файлу.")
                return

            # --- ВИКОРИСТАННЯ project_manager ---
            self.project_manager.load_project(new_project_data)
            # --- КІНЕЦЬ ---
            self.scene.clear()
            self.undo_stack.clear()
            self.set_edit_mode(EDIT_MODE_SCENARIO) # Завжди починаємо зі сценаріїв
            self.current_selected_node = None

            # Завантажуємо перший сценарій, якщо він є
            first_id = self.project_manager.get_first_scenario_id()
            if first_id:
                 self.load_scenario_state(first_id)
            else:
                 self.active_scenario_id = None # Немає сценаріїв

            self.update_ui_from_project() # Оновлюємо весь UI
            self.props_widget.setEnabled(False)
            self._update_window_title()

            self.show_status_message(f"Проект успішно імпортовано з {path}", color="green")
            log.info("Project imported successfully.")

        except Exception as e:
            log.critical(f"An unhandled exception occurred during project import: {e}", exc_info=True)
            QMessageBox.critical(self, "Помилка імпорту", f"Не вдалося імпортувати проект:\n{e}")
            self.new_project() # Скидаємо до нового проекту при критичній помилці

    def export_project(self):
        self.save_current_state() # Зберігаємо поточний стан перед експортом
        path, _ = QFileDialog.getSaveFileName(self, "Експорт проекту", "", "XML Files (*.xml)")
        if not path: return
        try:
            # --- ВИКОРИСТАННЯ serialization.py та project_manager ---
            project_data_to_save = self.project_manager.get_project_data()
            export_project_data(path, project_data_to_save)
            # --- КІНЕЦЬ ---
            self.show_status_message(f"Проект успішно експортовано до {path}", color="green")
        except Exception as e:
            log.error(f"Failed to export project: {e}", exc_info=True)
            QMessageBox.critical(self, "Помилка експорту", f"Не вдалося експортувати проект:\n{e}")

    # --- Other UI Actions ---
    def add_comment(self):
        if self.current_edit_mode == EDIT_MODE_SCENARIO and self.active_scenario_id is None: return
        if self.current_edit_mode == EDIT_MODE_MACRO and self.active_macro_id is None: return
        center_pos = self.view.mapToScene(self.view.viewport().rect().center())
        # --- Імпорт команди ---
        from commands import AddCommentCommand
        command = AddCommentCommand(self.scene, center_pos, self.view)
        self.undo_stack.push(command)

    def show_status_message(self, message, timeout=4000, color=None):
        # Логіка без змін
        style = f"color: {color};" if color else ""
        self.statusBar().setStyleSheet(style)
        self.statusBar().showMessage(message, timeout)
        if color: QTimer.singleShot(timeout, lambda: self.statusBar().setStyleSheet(""))
