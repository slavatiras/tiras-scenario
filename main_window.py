import sys
import uuid
import logging
from lxml import etree as ET
from PyQt6.QtWidgets import (
    QMainWindow, QGraphicsScene, QDockWidget, QListWidget, QWidget,
    QLabel, QLineEdit, QFormLayout, QFileDialog, QTextEdit,
    QVBoxLayout, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QHBoxLayout, QComboBox, QMessageBox, QListWidgetItem,
    QApplication, QToolBar, QTabWidget, QSpinBox, QGridLayout, QCheckBox,
    QScrollArea, QInputDialog, QSpacerItem, QSizePolicy
)
from PyQt6.QtGui import QColor, QAction, QUndoStack, QFont, QIcon
from PyQt6.QtCore import Qt, QTimer

from nodes import (BaseNode, Connection, CommentItem, FrameItem, NODE_REGISTRY, TriggerNode,
                   ActivateOutputNode, DeactivateOutputNode, DelayNode, SendSMSNode,
                   ConditionNodeZoneState, RepeatNode, SequenceNode, MacroNode,
                   MacroInputNode, MacroOutputNode)
# --- ИСПРАВЛЕНО: Убираем импорт commands отсюда, чтобы избежать цикла ---
# from commands import (AddNodeCommand, AddCommentCommand, RemoveItemsCommand,
#                       ChangePropertiesCommand, PasteCommand)
from editor_view import EditorView
from simulator import ScenarioSimulator
# --- ИСПРАВЛЕНО: Импортируем константы из нового файла ---
from constants import EDIT_MODE_SCENARIO, EDIT_MODE_MACRO


log = logging.getLogger(__name__)

DEVICE_SPECS = {
    "MOUT8R": {"type": "Модуль релейних виходів", "outputs": 8, "zones": 0},
    "PUIZ 2": {"type": "Пристрій індикації", "outputs": 0, "zones": 2},
    "ППКП Tiras-8L": {"type": "Базовий прилад", "outputs": 2, "zones": 8}
}

# --- ИСПРАВЛЕНО: Убираем определение констант отсюда ---
# # Определяем режимы редактирования
# EDIT_MODE_SCENARIO = 0
# EDIT_MODE_MACRO = 1


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Редактор сценаріїв Tiras");
        self.setGeometry(100, 100, 1400, 900)
        self.project_data = {}
        self.active_scenario_id = None;
        self.active_macro_id = None  # ID макроса, который сейчас редактируется
        self.current_edit_mode = EDIT_MODE_SCENARIO # Текущий режим
        self.previous_scenario_id = None # Для возврата из макроса
        self.current_selected_node = None
        self._old_scenario_name = None
        self._old_macro_name = None # Для переименования макросов
        self.undo_stack = QUndoStack(self)
        self.props_apply_timer = QTimer(self)
        self.props_apply_timer.setSingleShot(True)
        self.props_apply_timer.setInterval(750)
        self.props_apply_timer.timeout.connect(self.on_apply_button_clicked)
        self.scene = QGraphicsScene();
        self.scene.setBackgroundBrush(QColor("#333"))
        self.view = EditorView(self.scene, self.undo_stack, self);
        self.simulator = ScenarioSimulator(self.scene, self)
        self.setCentralWidget(self.view)
        self._create_actions();
        self._create_menu_bar();
        # Toolbars создаются динамически в _update_toolbars
        self._create_simulation_toolbar()
        self._create_panels()
        self._update_toolbars() # Создаем начальный toolbar
        self.scene.selectionChanged.connect(self.on_selection_changed)
        self.undo_stack.indexChanged.connect(lambda: QTimer.singleShot(1, self.validate_current_view))
        self.undo_stack.indexChanged.connect(self._update_simulation_trigger_zones)
        self.new_project()
        self.statusBar().showMessage("Готово")
        self._update_window_title()

    def _update_window_title(self):
        base_title = "Редактор сценаріїв Tiras"
        if self.current_edit_mode == EDIT_MODE_MACRO and self.active_macro_id:
            macro_name = self.project_data.get('macros', {}).get(self.active_macro_id, {}).get('name', self.active_macro_id)
            self.setWindowTitle(f"{base_title} - [Макрос: {macro_name}]")
        elif self.current_edit_mode == EDIT_MODE_SCENARIO and self.active_scenario_id:
            self.setWindowTitle(f"{base_title} - [{self.active_scenario_id}]")
        else:
            self.setWindowTitle(base_title)

    def _create_actions(self):
        self.new_action = QAction("&Новий проект", self);
        self.new_action.triggered.connect(self.new_project)
        self.import_action = QAction("&Імпорт...", self);
        self.import_action.triggered.connect(self.import_project)
        self.export_action = QAction("&Експорт...", self);
        self.export_action.triggered.connect(self.export_project)
        self.undo_action = self.undo_stack.createUndoAction(self, "&Скасувати")
        self.undo_action.setShortcut("Ctrl+Z")
        self.redo_action = self.undo_stack.createRedoAction(self, "&Повторити")
        self.redo_action.setShortcut("Ctrl+Y")
        self.copy_action = QAction("&Копіювати", self)
        self.copy_action.setShortcut("Ctrl+C")
        self.copy_action.triggered.connect(self.copy_selection)
        self.paste_action = QAction("&Вставити", self)
        self.paste_action.setShortcut("Ctrl+V")
        self.paste_action.triggered.connect(self.paste_at_center)
        self.add_comment_action = QAction("Додати коментар", self)
        self.add_comment_action.triggered.connect(self.add_comment)
        # Действие для возврата из макроса
        self.back_to_scenario_action = QAction("Повернутись до сценарію", self)
        self.back_to_scenario_action.triggered.connect(self.return_to_scenario)

    def _create_menu_bar(self):
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
        # Добавляем действие возврата в меню Вид или Правка (пока в Правку)
        edit_menu.addSeparator()
        edit_menu.addAction(self.back_to_scenario_action)

    def _update_toolbars(self):
        # Удаляем существующие панели инструментов узлов
        for toolbar in self.findChildren(QToolBar):
            if toolbar.objectName() in ["scenario_toolbar", "macro_toolbar"]:
                self.removeToolBar(toolbar)
                toolbar.deleteLater()

        if self.current_edit_mode == EDIT_MODE_SCENARIO:
            self._create_scenario_toolbar()
            self.back_to_scenario_action.setEnabled(False) # Нельзя вернуться из сценария
        elif self.current_edit_mode == EDIT_MODE_MACRO:
            self._create_macro_toolbar()
            self.back_to_scenario_action.setEnabled(True) # Можно вернуться из макроса

        # Обновляем видимость панели симуляции
        self.sim_toolbar.setVisible(self.current_edit_mode == EDIT_MODE_SCENARIO)


    def _create_scenario_toolbar(self):
        self.scenario_toolbar = QToolBar("Основна панель")
        self.scenario_toolbar.setObjectName("scenario_toolbar")
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.scenario_toolbar)
        INTERNAL_NODE_NAMES = ["Вхід Макроса", "Вихід Макроса"]

        for node_type in sorted(NODE_REGISTRY.keys()):
            if node_type in INTERNAL_NODE_NAMES:
                continue
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

        # Кнопка "Назад"
        back_action = QAction("⬅️ Повернутись", self)
        back_action.triggered.connect(self.return_to_scenario)
        self.macro_toolbar.addAction(back_action)
        self.macro_toolbar.addSeparator()

        # Кнопки для добавления Входа и Выхода
        for node_type in ["Вхід Макроса", "Вихід Макроса"]:
             node_class = NODE_REGISTRY[node_type]
             icon = getattr(node_class, 'ICON', '●')
             action = QAction(icon, self)
             action.setToolTip(f"Додати вузол '{node_type}'")
             action.triggered.connect(lambda checked=False, nt=node_type: self._on_toolbar_action_triggered(nt))
             self.macro_toolbar.addAction(action)

        # Можно добавить и другие полезные узлы для макросов (Delay, Condition и т.д.)
        # self.macro_toolbar.addSeparator()
        # for node_type in ["Затримка", "Умова: Стан зони"]: # Пример
        #      node_class = NODE_REGISTRY[node_type]
        #      icon = getattr(node_class, 'ICON', '●')
        #      action = QAction(icon, self)
        #      action.setToolTip(f"Додати вузол '{node_type}'")
        #      action.triggered.connect(lambda checked=False, nt=node_type: self._on_toolbar_action_triggered(nt))
        #      self.macro_toolbar.addAction(action)


    def _create_simulation_toolbar(self):
        self.sim_toolbar = QToolBar("Панель симуляції")
        self.sim_toolbar.setObjectName("simulation_toolbar")
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.sim_toolbar)
        # ... (содержимое панели симуляции остается прежним)
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


    def _on_toolbar_action_triggered(self, node_type):
        self.add_node(node_type, self.view.mapToScene(self.view.viewport().rect().center()))

    def _create_panels(self):
        # --- Панель Проекта (Сценарии и Макросы) ---
        project_dock = QDockWidget("Проект", self)
        self.project_tabs = QTabWidget()

        # Вкладка Сценарии
        scenarios_widget = QWidget()
        scenarios_layout = QVBoxLayout(scenarios_widget)
        self.scenarios_list = QListWidget()
        scenarios_layout.addWidget(self.scenarios_list)
        scenarios_btn_layout = QHBoxLayout()
        add_scenario_btn = QPushButton("Додати")
        remove_scenario_btn = QPushButton("Видалити")
        scenarios_btn_layout.addWidget(add_scenario_btn)
        scenarios_btn_layout.addWidget(remove_scenario_btn)
        scenarios_layout.addLayout(scenarios_btn_layout)

        # Вкладка Макросы
        macros_widget = QWidget()
        macros_layout = QVBoxLayout(macros_widget)
        self.macros_list = QListWidget()
        macros_layout.addWidget(self.macros_list)
        macros_btn_layout = QHBoxLayout()
        remove_macro_btn = QPushButton("Видалити")
        rename_macro_btn = QPushButton("Перейменувати") # Добавлена кнопка
        macros_btn_layout.addWidget(rename_macro_btn) # Добавлена кнопка
        macros_btn_layout.addWidget(remove_macro_btn)
        macros_layout.addLayout(macros_btn_layout)

        self.project_tabs.addTab(scenarios_widget, "Сценарії")
        self.project_tabs.addTab(macros_widget, "Макроси")
        project_dock.setWidget(self.project_tabs)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, project_dock)


        nodes_dock = QDockWidget("Елементи сценарію", self);
        self.nodes_list = QListWidget()
        # Обновляем список доступных узлов при смене режима
        self._update_nodes_list()
        nodes_dock.setWidget(self.nodes_list)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, nodes_dock)

        config_dock = QDockWidget("Конфігурація системи", self);
        config_tabs = QTabWidget()
        # --- (Содержимое config_dock остается прежним) ---
        devices_widget = QWidget()
        devices_layout = QVBoxLayout(devices_widget)
        self.devices_table = QTableWidget(0, 3)
        self.devices_table.setHorizontalHeaderLabels(["ID", "Назва пристрою", "Тип"])
        self.devices_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.devices_table.hideColumn(0)
        devices_layout.addWidget(self.devices_table)
        add_device_layout = QHBoxLayout()
        self.device_type_combo = QComboBox()
        self.device_type_combo.addItems(DEVICE_SPECS.keys())
        add_device_btn = QPushButton("Додати пристрій")
        remove_device_btn = QPushButton("Видалити пристрій")
        add_device_layout.addWidget(self.device_type_combo)
        add_device_layout.addWidget(add_device_btn)
        add_device_layout.addWidget(remove_device_btn)
        devices_layout.addLayout(add_device_layout)

        zones_widget = QWidget()
        zones_layout = QVBoxLayout(zones_widget)
        self.zones_table = QTableWidget(0, 3)
        self.zones_table.setHorizontalHeaderLabels(["ID", "Пристрій", "Назва зони"])
        self.zones_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.zones_table.hideColumn(0)
        zones_layout.addWidget(self.zones_table)

        outputs_widget = QWidget()
        outputs_layout = QVBoxLayout(outputs_widget)
        self.outputs_table = QTableWidget(0, 3)
        self.outputs_table.setHorizontalHeaderLabels(["ID", "Пристрій", "Назва виходу"])
        self.outputs_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.outputs_table.hideColumn(0)
        outputs_layout.addWidget(self.outputs_table)

        users_widget = QWidget()
        users_layout = QVBoxLayout(users_widget)
        self.users_table = QTableWidget(0, 3)
        self.users_table.setHorizontalHeaderLabels(["ID", "Ім'я користувача", "Телефон"])
        self.users_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.users_table.hideColumn(0)
        users_layout.addWidget(self.users_table)
        users_btn_layout = QHBoxLayout()
        add_user_btn = QPushButton("Додати")
        remove_user_btn = QPushButton("Видалити")
        users_btn_layout.addWidget(add_user_btn);
        users_btn_layout.addWidget(remove_user_btn)
        users_layout.addLayout(users_btn_layout)

        config_tabs.addTab(devices_widget, "Пристрої")
        config_tabs.addTab(zones_widget, "Зони")
        config_tabs.addTab(outputs_widget, "Виходи")
        config_tabs.addTab(users_widget, "Користувачі")
        config_dock.setWidget(config_tabs)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, config_dock)

        props_dock = QDockWidget("Властивості", self)
        self.props_widget = QWidget();
        self.main_props_layout = QVBoxLayout(self.props_widget);
        props_dock.setWidget(self.props_widget)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, props_dock)
        self.setup_properties_panel();
        self._update_properties_panel_ui()
        self.props_widget.setEnabled(False)

        # Подключения сигналов
        add_scenario_btn.clicked.connect(lambda: self.add_scenario())
        remove_scenario_btn.clicked.connect(self.remove_scenario)
        remove_macro_btn.clicked.connect(self.remove_macro)
        rename_macro_btn.clicked.connect(self.rename_macro) # Добавлено
        self.scenarios_list.currentItemChanged.connect(self.on_active_scenario_changed)
        self.scenarios_list.itemDoubleClicked.connect(self.on_scenario_item_double_clicked)
        self.scenarios_list.itemChanged.connect(self.on_scenario_renamed)
        self.macros_list.itemDoubleClicked.connect(self.on_macro_item_double_clicked) # Подключаем двойной клик для макросов
        self.nodes_list.itemClicked.connect(self.on_node_list_clicked)
        add_device_btn.clicked.connect(self.add_device)
        remove_device_btn.clicked.connect(self.remove_device)
        add_user_btn.clicked.connect(lambda: self.add_config_item('users'))
        remove_user_btn.clicked.connect(lambda: self.remove_config_item('users'))
        self.devices_table.itemChanged.connect(self.on_config_table_changed)
        self.zones_table.itemChanged.connect(self.on_config_table_changed)
        self.outputs_table.itemChanged.connect(self.on_config_table_changed)
        self.users_table.itemChanged.connect(self.on_config_table_changed)
        self.project_tabs.currentChanged.connect(self.on_project_tab_changed) # Смена вкладки Проекта

    def on_project_tab_changed(self, index):
        # Если переключились на вкладку сценариев и редактировали макрос, возвращаемся
        if index == 0 and self.current_edit_mode == EDIT_MODE_MACRO:
            self.return_to_scenario()
        # Если переключились на вкладку макросов, возможно, ничего не делаем
        # (вход в макрос по двойному клику)

    def _update_nodes_list(self):
        """Обновляет список доступных узлов в зависимости от режима."""
        self.nodes_list.clear()
        if self.current_edit_mode == EDIT_MODE_SCENARIO:
            # Показываем все узлы, кроме внутренних макросных
            items = sorted([name for name in NODE_REGISTRY.keys() if name not in ["Вхід Макроса", "Вихід Макроса"]])
        elif self.current_edit_mode == EDIT_MODE_MACRO:
            # Показываем только узлы входа/выхода и, возможно, базовые (опционально)
            items = sorted(["Вхід Макроса", "Вихід Макроса", "Затримка", "Умова: Стан зони", "Повтор"]) # Пример
        self.nodes_list.addItems(items)

    def setup_properties_panel(self):
        # --- (Код setup_properties_panel остается прежним, добавляется только macro_props_widget) ---
        while self.main_props_layout.count():
            item = self.main_props_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
            else: # Handle layout items or spacers
                layout_item = item.layout()
                if layout_item:
                    # Recursively delete widgets in the layout
                    while layout_item.count():
                        child = layout_item.takeAt(0)
                        if child.widget():
                            child.widget().deleteLater()
                    layout_item.deleteLater()
                elif item.spacerItem():
                     pass # Spacers don't need deletion

        self.base_props_widget = QWidget()
        base_props_layout = QFormLayout(self.base_props_widget)
        self.prop_name = QLineEdit();
        self.prop_description = QTextEdit();
        self.prop_description.setFixedHeight(80)
        base_props_layout.addRow("Назва:", self.prop_name);
        base_props_layout.addRow("Опис:", self.prop_description)
        self.main_props_layout.addWidget(self.base_props_widget)
        self.prop_name.textChanged.connect(self._schedule_properties_apply)
        self.prop_description.textChanged.connect(self._schedule_properties_apply)

        self.trigger_props_widget = QWidget()
        trigger_layout = QFormLayout(self.trigger_props_widget)
        self.trigger_type_combo = QComboBox()
        self.trigger_type_combo.addItems(["Пожежа", "Тривога", "Несправність 220В", "Зняття з охорони"])
        trigger_layout.addRow("Спосіб запуску:", self.trigger_type_combo)
        self.zones_container = QWidget()
        self.zones_layout = QGridLayout(self.zones_container)
        self.zones_layout.setContentsMargins(0, 0, 0, 0)
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(self.zones_container)
        scroll_area.setMinimumHeight(100)
        trigger_layout.addRow("Зони:", scroll_area)
        self.main_props_layout.addWidget(self.trigger_props_widget)
        self.trigger_type_combo.currentTextChanged.connect(self._schedule_properties_apply)

        self.output_props_widget = QWidget()
        output_layout = QFormLayout(self.output_props_widget)
        self.output_select_combo = QComboBox()
        output_layout.addRow("Вихід:", self.output_select_combo)
        self.main_props_layout.addWidget(self.output_props_widget)
        self.output_select_combo.currentIndexChanged.connect(self._schedule_properties_apply)

        self.delay_props_widget = QWidget()
        delay_layout = QFormLayout(self.delay_props_widget)
        self.delay_spinbox = QSpinBox()
        self.delay_spinbox.setRange(0, 3600);
        self.delay_spinbox.setSuffix(" сек.")
        delay_layout.addRow("Час затримки:", self.delay_spinbox)
        self.main_props_layout.addWidget(self.delay_props_widget)
        self.delay_spinbox.valueChanged.connect(self._schedule_properties_apply)

        self.sms_props_widget = QWidget()
        sms_layout = QFormLayout(self.sms_props_widget)
        self.sms_user_combo = QComboBox()
        self.sms_message_text = QLineEdit()
        sms_layout.addRow("Користувач:", self.sms_user_combo)
        sms_layout.addRow("Повідомлення:", self.sms_message_text)
        self.main_props_layout.addWidget(self.sms_props_widget)
        self.sms_user_combo.currentIndexChanged.connect(self._schedule_properties_apply)
        self.sms_message_text.textChanged.connect(self._schedule_properties_apply)

        self.condition_props_widget = QWidget()
        condition_layout = QFormLayout(self.condition_props_widget)
        self.condition_zone_combo = QComboBox()
        self.condition_state_combo = QComboBox()
        self.condition_state_combo.addItems(["Під охороною", "Знята з охорони", "Тривога"])
        condition_layout.addRow("Зона:", self.condition_zone_combo)
        condition_layout.addRow("Перевірити стан:", self.condition_state_combo)
        self.main_props_layout.addWidget(self.condition_props_widget)
        self.condition_zone_combo.currentIndexChanged.connect(self._schedule_properties_apply)
        self.condition_state_combo.currentIndexChanged.connect(self._schedule_properties_apply)

        self.repeat_props_widget = QWidget()
        repeat_layout = QFormLayout(self.repeat_props_widget)
        self.repeat_count_spinbox = QSpinBox()
        self.repeat_count_spinbox.setRange(-1, 100)
        self.repeat_count_spinbox.setSpecialValueText("Безкінечно")
        repeat_layout.addRow("Кількість повторів:", self.repeat_count_spinbox)
        self.main_props_layout.addWidget(self.repeat_props_widget)
        self.repeat_count_spinbox.valueChanged.connect(self._schedule_properties_apply)

        # Панель для вузла Макроса
        self.macro_props_widget = QWidget()
        macro_layout = QFormLayout(self.macro_props_widget)
        self.macro_definition_combo = QComboBox()
        macro_layout.addRow("Визначення:", self.macro_definition_combo)
        self.main_props_layout.addWidget(self.macro_props_widget)
        self.macro_definition_combo.currentIndexChanged.connect(self._schedule_properties_apply)

        # Добавляем растягивающийся элемент в конец
        spacer = QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        self.main_props_layout.addSpacerItem(spacer)


    def _schedule_properties_apply(self):
        if self.props_widget.isEnabled():
            self.props_apply_timer.start()

    def on_selection_changed(self):
        if self.simulator.is_running:
            return
        self.props_apply_timer.stop()
        selected_items = self.scene.selectedItems()

        newly_selected_node = None
        if len(selected_items) == 1 and isinstance(selected_items[0], BaseNode):
            newly_selected_node = selected_items[0]

        # Если ничего не выбрано, но был выбранный узел, сбрасываем его
        if not newly_selected_node:
             self.current_selected_node = None
        # Иначе обновляем текущий выбранный узел
        elif newly_selected_node != self.current_selected_node:
             self.current_selected_node = newly_selected_node

        # Включаем/выключаем панель в зависимости от того, выбран ли узел
        self.props_widget.setEnabled(self.current_selected_node is not None)
        self._update_properties_panel_ui()


    def _update_properties_panel_ui(self):
        node = self.current_selected_node if self.props_widget.isEnabled() else None
        is_node_selected = node is not None

        # Скрываем все специфичные панели
        self.trigger_props_widget.setVisible(False)
        self.output_props_widget.setVisible(False)
        self.delay_props_widget.setVisible(False)
        self.sms_props_widget.setVisible(False)
        self.condition_props_widget.setVisible(False)
        self.repeat_props_widget.setVisible(False)
        self.macro_props_widget.setVisible(False)

        # Показываем базовую панель, если что-то выбрано
        self.base_props_widget.setVisible(is_node_selected)
        # Отключаем редактирование имени для узлов Вход/Выход макроса
        is_macro_io_node = isinstance(node, (MacroInputNode, MacroOutputNode))
        self.prop_name.setEnabled(not is_macro_io_node)


        if is_node_selected:
            self.prop_name.blockSignals(True)
            self.prop_description.blockSignals(True)
            self.prop_name.setText(node.node_name)
            self.prop_description.setPlainText(node.description)
            self.prop_name.blockSignals(False)
            self.prop_description.blockSignals(False)

            # Показываем нужную специфичную панель
            if isinstance(node, TriggerNode): self._update_trigger_props_ui(); self.trigger_props_widget.setVisible(True)
            elif isinstance(node, (ActivateOutputNode, DeactivateOutputNode)): self._update_output_props_ui(); self.output_props_widget.setVisible(True)
            elif isinstance(node, DelayNode): self._update_delay_props_ui(); self.delay_props_widget.setVisible(True)
            elif isinstance(node, SendSMSNode): self._update_sms_props_ui(); self.sms_props_widget.setVisible(True)
            elif isinstance(node, ConditionNodeZoneState): self._update_condition_props_ui(); self.condition_props_widget.setVisible(True)
            elif isinstance(node, RepeatNode): self._update_repeat_props_ui(); self.repeat_props_widget.setVisible(True)
            elif isinstance(node, MacroNode): self._update_macro_props_ui(); self.macro_props_widget.setVisible(True)

        else:
            self.prop_name.clear()
            self.prop_description.clear()

    # --- (Методы _get_all_zones..., _update_..._props_ui остаются прежними) ---
    def _get_all_zones_and_outputs_from_devices(self):
        all_zones, all_outputs = [], []
        # Добавлена проверка на наличие 'config'
        if 'config' in self.project_data:
            for device in self.project_data['config'].get('devices', []):
                all_zones.extend(device.get('zones', []))
                all_outputs.extend(device.get('outputs', []))
        return all_zones, all_outputs

    def _update_trigger_props_ui(self):
        node = self.current_selected_node
        if not node: return
        props = dict(node.properties)
        self.trigger_type_combo.blockSignals(True)
        self.trigger_type_combo.setCurrentText(props.get('trigger_type', 'Пожежа'))
        self.trigger_type_combo.blockSignals(False)
        while self.zones_layout.count():
            child = self.zones_layout.takeAt(0)
            if child.widget(): child.widget().deleteLater()
        selected_zones = props.get('zones', [])
        all_zones, _ = self._get_all_zones_and_outputs_from_devices()
        for i, zone in enumerate(all_zones):
            checkbox = QCheckBox(f"{zone['parent_name']}: {zone['name']}")
            checkbox.setChecked(zone['id'] in selected_zones)
            checkbox.toggled.connect(self._schedule_properties_apply)
            checkbox.setProperty("zone_id", zone['id'])
            self.zones_layout.addWidget(checkbox, i // 2, i % 2)

    def _update_output_props_ui(self):
        node = self.current_selected_node
        if not node: return
        props = dict(node.properties)
        self.output_select_combo.blockSignals(True)
        self.output_select_combo.clear()
        self.output_select_combo.addItem("Не призначено", userData=None)
        _, all_outputs = self._get_all_zones_and_outputs_from_devices()
        for output in all_outputs:
            self.output_select_combo.addItem(f"{output['parent_name']}: {output['name']}", userData=output['id'])
        selected_id = props.get('output_id')
        index = self.output_select_combo.findData(selected_id) if selected_id else 0
        self.output_select_combo.setCurrentIndex(max(0, index))
        self.output_select_combo.blockSignals(False)

    def _update_delay_props_ui(self):
        node = self.current_selected_node
        if not node: return
        props = dict(node.properties)
        self.delay_spinbox.blockSignals(True)
        self.delay_spinbox.setValue(int(props.get('seconds', 0)))
        self.delay_spinbox.blockSignals(False)

    def _update_sms_props_ui(self):
        node = self.current_selected_node
        if not node: return
        props = dict(node.properties)
        self.sms_user_combo.blockSignals(True)
        self.sms_user_combo.clear()
        self.sms_user_combo.addItem("Не призначено", userData=None)
        for user in self.project_data['config'].get('users', []):
            self.sms_user_combo.addItem(user['name'], userData=user['id'])
        selected_id = props.get('user_id')
        index = self.sms_user_combo.findData(selected_id) if selected_id else 0
        self.sms_user_combo.setCurrentIndex(max(0, index))
        self.sms_user_combo.blockSignals(False)
        self.sms_message_text.blockSignals(True)
        self.sms_message_text.setText(props.get('message', ''))
        self.sms_message_text.blockSignals(False)

    def _update_condition_props_ui(self):
        node = self.current_selected_node
        if not node: return
        props = dict(node.properties)
        self.condition_zone_combo.blockSignals(True)
        self.condition_zone_combo.clear()
        self.condition_zone_combo.addItem("Не призначено", userData=None)
        all_zones, _ = self._get_all_zones_and_outputs_from_devices()
        for zone in all_zones:
            self.condition_zone_combo.addItem(f"{zone['parent_name']}: {zone['name']}", userData=zone['id'])
        selected_id = props.get('zone_id')
        index = self.condition_zone_combo.findData(selected_id) if selected_id else 0
        self.condition_zone_combo.setCurrentIndex(max(0, index))
        self.condition_zone_combo.blockSignals(False)
        self.condition_state_combo.blockSignals(True)
        self.condition_state_combo.setCurrentText(props.get('state', 'Під охороною'))
        self.condition_state_combo.blockSignals(False)

    def _update_repeat_props_ui(self):
        node = self.current_selected_node
        if not node: return
        props = dict(node.properties)
        self.repeat_count_spinbox.blockSignals(True)
        self.repeat_count_spinbox.setValue(int(props.get('count', 3)))
        self.repeat_count_spinbox.blockSignals(False)

    def _update_macro_props_ui(self):
        node = self.current_selected_node
        if not isinstance(node, MacroNode): return

        self.macro_definition_combo.blockSignals(True)
        self.macro_definition_combo.clear()
        self.macro_definition_combo.addItem("Не призначено", userData=None)
        macros = self.project_data.get('macros', {})
        for macro_id, macro_data in macros.items():
            self.macro_definition_combo.addItem(macro_data.get('name', macro_id), userData=macro_id)

        selected_id = node.macro_id
        index = self.macro_definition_combo.findData(selected_id) if selected_id else 0
        self.macro_definition_combo.setCurrentIndex(max(0, index))
        self.macro_definition_combo.blockSignals(False)

    def on_apply_button_clicked(self):
        if not self.current_selected_node: return
        node = self.current_selected_node
        old_name = node.node_name
        old_desc = node.description
        old_props = [p for p in node.properties] # Копируем список
        old_macro_id = getattr(node, 'macro_id', None) # Безопасное получение

        new_name = self.prop_name.text()
        new_desc = self.prop_description.toPlainText()
        new_props_list = old_props # По умолчанию сохраняем старые
        new_macro_id = old_macro_id

        # Обработка специфичных свойств
        if isinstance(node, TriggerNode):
            trigger_type = self.trigger_type_combo.currentText()
            selected_zones = []
            for i in range(self.zones_layout.count()):
                widget = self.zones_layout.itemAt(i).widget()
                if isinstance(widget, QCheckBox) and widget.isChecked():
                    selected_zones.append(widget.property("zone_id"))
            new_props_list = [('trigger_type', trigger_type), ('zones', selected_zones)]
        # ... (остальные типы узлов как были)
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
            new_macro_id = self.macro_definition_combo.currentData() or None # None если "Не призначено"
        elif isinstance(node, (MacroInputNode, MacroOutputNode)):
             # Для входов/выходов макроса имя берется из prop_name, но не меняется здесь
             new_name = old_name # Имя не меняем через общую панель
             # TODO: Нужно ли здесь обновлять имя в определении макроса? Пока нет.

        old_data = {'name': old_name, 'desc': old_desc, 'props': old_props, 'macro_id': old_macro_id}
        new_data = {'name': new_name, 'desc': new_desc, 'props': new_props_list, 'macro_id': new_macro_id}

        if old_data != new_data:
            # --- ИСПРАВЛЕНО: Импортируем ChangePropertiesCommand здесь ---
            from commands import ChangePropertiesCommand
            command = ChangePropertiesCommand(node, old_data, new_data)
            self.undo_stack.push(command)


    # --- (Методы add_device, remove_device, add_config_item, remove_config_item, on_config_table_changed остаются) ---
    def update_config_ui(self):
        config = self.project_data.get('config', {})
        self.devices_table.blockSignals(True)
        self.zones_table.blockSignals(True)
        self.outputs_table.blockSignals(True)
        self.users_table.blockSignals(True)

        self.devices_table.setRowCount(0)
        for device in config.get('devices', []):
            row = self.devices_table.rowCount()
            self.devices_table.insertRow(row)
            self.devices_table.setItem(row, 0, QTableWidgetItem(device['id']))
            self.devices_table.setItem(row, 1, QTableWidgetItem(device['name']))
            self.devices_table.setItem(row, 2, QTableWidgetItem(device['type']))

        all_zones, all_outputs = self._get_all_zones_and_outputs_from_devices()
        self.zones_table.setRowCount(0)
        for zone in all_zones:
            row = self.zones_table.rowCount()
            self.zones_table.insertRow(row)
            self.zones_table.setItem(row, 0, QTableWidgetItem(zone['id']))
            self.zones_table.setItem(row, 1, QTableWidgetItem(zone['parent_name']))
            self.zones_table.setItem(row, 2, QTableWidgetItem(zone['name']))

        self.outputs_table.setRowCount(0)
        for output in all_outputs:
            row = self.outputs_table.rowCount()
            self.outputs_table.insertRow(row)
            self.outputs_table.setItem(row, 0, QTableWidgetItem(output['id']))
            self.outputs_table.setItem(row, 1, QTableWidgetItem(output['parent_name']))
            self.outputs_table.setItem(row, 2, QTableWidgetItem(output['name']))

        self.users_table.setRowCount(0)
        for item in config.get('users', []):
            row = self.users_table.rowCount()
            self.users_table.insertRow(row)
            self.users_table.setItem(row, 0, QTableWidgetItem(item['id']))
            self.users_table.setItem(row, 1, QTableWidgetItem(item['name']))
            self.users_table.setItem(row, 2, QTableWidgetItem(item.get('phone', '')))

        self.devices_table.blockSignals(False)
        self.zones_table.blockSignals(False)
        self.outputs_table.blockSignals(False)
        self.users_table.blockSignals(False)
        self.validate_current_view() # Используем новый метод валидации
        if self.current_selected_node: self.on_selection_changed()

    def add_device(self):
        device_type = self.device_type_combo.currentText()
        spec = DEVICE_SPECS[device_type]
        # Обеспечиваем наличие 'devices' в 'config'
        if 'devices' not in self.project_data['config']:
            self.project_data['config']['devices'] = []
        device_count = len([d for d in self.project_data['config']['devices'] if d['type'] == device_type])
        new_device = {'id': str(uuid.uuid4()), 'name': f"{device_type} #{device_count + 1}", 'type': device_type,
                      'zones': [], 'outputs': []}
        for i in range(spec['zones']): new_device['zones'].append(
            {'id': str(uuid.uuid4()), 'name': f"Зона {i + 1}", 'parent_name': new_device['name']})
        for i in range(spec['outputs']): new_device['outputs'].append(
            {'id': str(uuid.uuid4()), 'name': f"Вихід {i + 1}", 'parent_name': new_device['name']})
        self.project_data['config']['devices'].append(new_device)
        self.update_config_ui()

    def remove_device(self):
        selected_rows = sorted(list(set(index.row() for index in self.devices_table.selectedIndexes())), reverse=True)
        if not selected_rows: return
        reply = QMessageBox.question(self, "Підтвердження видалення",
                                     f"Ви впевнені, що хочете видалити {len(selected_rows)} пристрій(ої)?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            for row in selected_rows: del self.project_data['config']['devices'][row]
            self.update_config_ui()

    def add_config_item(self, config_key):
        if config_key != 'users': return
        # Обеспечиваем наличие ключа
        self.project_data['config'].setdefault(config_key, [])
        table, name_prefix, extra_data = self.users_table, "Новий користувач", {'phone': ''}
        new_item = {'id': str(uuid.uuid4()), 'name': f"{name_prefix} {table.rowCount() + 1}", **extra_data}
        self.project_data['config'][config_key].append(new_item)
        self.update_config_ui()

    def remove_config_item(self, config_key):
        if config_key != 'users': return
        row = self.users_table.currentRow()
        if row > -1 and row < len(self.project_data['config'].get(config_key, [])):
            del self.project_data['config'][config_key][row]
            self.update_config_ui()

    def on_config_table_changed(self, item):
        table = item.tableWidget()
        row, col = item.row(), item.column()
        # Проверяем, существует ли item
        id_item = table.item(row, 0)
        if not id_item: return
        item_id = id_item.text()

        if table is self.devices_table:
            for device in self.project_data['config']['devices']:
                if device['id'] == item_id:
                    device['name'] = item.text()
                    for zone in device.get('zones', []): zone['parent_name'] = device['name']
                    for output in device.get('outputs', []): output['parent_name'] = device['name']
                    break
        elif table is self.zones_table:
            for device in self.project_data['config']['devices']:
                for zone in device.get('zones', []):
                    if zone['id'] == item_id: zone['name'] = item.text(); break
        elif table is self.outputs_table:
            for device in self.project_data['config']['devices']:
                for output in device.get('outputs', []):
                    if output['id'] == item_id: output['name'] = item.text(); break
        elif table is self.users_table:
            data_key = 'name' if col == 1 else 'phone'
            for user in self.project_data['config']['users']:
                if user['id'] == item_id: user[data_key] = item.text(); break
        # Вызываем обновление UI один раз после всех изменений
        QTimer.singleShot(0, self.update_config_ui)

    def add_node(self, node_type, position):
        # --- ИСПРАВЛЕНО: Импортируем AddNodeCommand здесь ---
        from commands import AddNodeCommand

        if self.current_edit_mode == EDIT_MODE_SCENARIO and self.active_scenario_id is None:
            self.show_status_message("Спочатку виберіть або створіть сценарій.", 5000, color="orange")
            return
        if self.current_edit_mode == EDIT_MODE_MACRO and self.active_macro_id is None:
            self.show_status_message("Неможливо додати вузол: не вибрано макрос для редагування.", 5000, color="red")
            return

        # Запрет добавления триггера в макрос
        if self.current_edit_mode == EDIT_MODE_MACRO and node_type == "Тригер":
             self.show_status_message("Помилка: Тригер не можна додавати всередині макросу.", 5000, color="red")
             return
        # Запрет добавления макро-узла в макрос (предотвращение рекурсии)
        if self.current_edit_mode == EDIT_MODE_MACRO and node_type == "Макрос":
             self.show_status_message("Помилка: Макрос не можна додавати всередині іншого макросу.", 5000, color="red")
             return

        # Проверка на дублирование MacroInput/Output в макросе
        if self.current_edit_mode == EDIT_MODE_MACRO:
             node_class = NODE_REGISTRY.get(node_type)
             if node_class in (MacroInputNode, MacroOutputNode):
                  existing_nodes = [item for item in self.scene.items() if isinstance(item, node_class)]
                  if existing_nodes:
                       # Можно разрешить несколько, но нужно будет управлять именами
                       self.show_status_message(f"Помилка: Вузол '{node_type}' вже існує в цьому макросі.", 5000, color="red")
                       return


        if node_type == "Тригер" and any(isinstance(i, TriggerNode) for i in self.scene.items()):
            self.show_status_message("Помилка: Тригер у сценарії може бути лише один.", 5000, color="red")
            return
        if node_type in NODE_REGISTRY:
            command = AddNodeCommand(self.scene, node_type, position)
            self.undo_stack.push(command)

    def on_node_list_clicked(self, item):
        self.add_node(item.text(), self.view.mapToScene(self.view.viewport().rect().center()))

    def new_project(self):
        self.scene.clear();
        self.project_data = {
            'scenarios': {},
            'macros': {}, # Добавляем раздел для макросов
            'config': {
                'devices': [],
                'users': [{'id': str(uuid.uuid4()), 'name': 'Адміністратор', 'phone': '+380000000000'}]
            }
        }
        self.device_type_combo.setCurrentText("ППКП Tiras-8L")
        self.add_device()
        self.active_scenario_id = None;
        self.active_macro_id = None
        self.current_edit_mode = EDIT_MODE_SCENARIO
        self.previous_scenario_id = None
        self.current_selected_node = None
        self.update_scenarios_list();
        self.update_macros_list()
        self.update_config_ui()
        self.props_widget.setEnabled(False)
        self.undo_stack.clear()
        self.add_scenario("Сценарій 1")
        self._update_toolbars()
        self._update_window_title()
        self._update_nodes_list()

    def add_scenario(self, name=None):
        if name is None:
            i = 1;
            name = "Новий сценарій"
            while name in self.project_data['scenarios']: name = f"Новий сценарій {i}"; i += 1
        if name in self.project_data['scenarios']: return
        self.project_data['scenarios'][name] = {'nodes': [], 'connections': [], 'comments': [], 'frames': []}
        self.update_scenarios_list()
        items = self.scenarios_list.findItems(name, Qt.MatchFlag.MatchExactly)
        if items: self.scenarios_list.setCurrentItem(items[0])

    def remove_scenario(self):
        current_item = self.scenarios_list.currentItem()
        if not current_item or self.scenarios_list.count() <= 1: return
        scenario_name = current_item.text()

        reply = QMessageBox.question(self, "Видалення Сценарію",
                                     f"Ви впевнені, що хочете видалити сценарій '{scenario_name}'?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.No: return

        del self.project_data['scenarios'][scenario_name]
        self.active_scenario_id = None
        self.update_scenarios_list();
        if self.scenarios_list.count() > 0:
            self.scenarios_list.setCurrentRow(0) # Переключаемся на первый
        else:
            self.scene.clear() # Очищаем сцену, если сценариев не осталось
            self.set_edit_mode(EDIT_MODE_SCENARIO) # На всякий случай
        self.undo_stack.clear()


    def remove_macro(self):
        current_item = self.macros_list.currentItem()
        if not current_item: return
        macro_name = current_item.text()
        macro_id_to_remove = current_item.data(Qt.ItemDataRole.UserRole)

        if not macro_id_to_remove: return

        # Проверка использования макроса
        usage_count = 0
        usage_scenarios = []
        for scenario_id, scenario_data in self.project_data.get('scenarios', {}).items():
            for node_data in scenario_data.get('nodes', []):
                 if node_data.get('node_type') == 'MacroNode' and node_data.get('macro_id') == macro_id_to_remove:
                      usage_count += 1
                      if scenario_id not in usage_scenarios:
                           usage_scenarios.append(scenario_id)

        if usage_count > 0:
             QMessageBox.warning(self, "Неможливо видалити макрос",
                                 f"Макрос '{macro_name}' використовується у {usage_count} вузлах сценаріїв:\n"
                                 f"{', '.join(usage_scenarios)}\nСпочатку видаліть ці вузли.")
             return

        reply = QMessageBox.question(self, "Видалення Макросу",
                                     f"Ви впевнені, що хочете видалити макрос '{macro_name}'?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.No: return


        if macro_id_to_remove:
            if self.active_macro_id == macro_id_to_remove:
                # Если удаляем текущий редактируемый макрос, возвращаемся к сценарию
                self.return_to_scenario(force_return=True)
            del self.project_data['macros'][macro_id_to_remove]
            self.update_macros_list()
            # Обновить MacroNode, если они ссылались на удаленный макрос?
            # Они должны стать невалидными при следующей валидации.

    def rename_macro(self):
        current_item = self.macros_list.currentItem()
        if not current_item: return
        macro_id = current_item.data(Qt.ItemDataRole.UserRole)
        if not macro_id: return

        macros = self.project_data.get('macros', {})
        current_name = macros.get(macro_id, {}).get('name', macro_id)

        new_name, ok = QInputDialog.getText(self, "Перейменувати Макрос",
                                            "Нове ім'я макросу:",
                                            QLineEdit.EchoMode.Normal, current_name)

        if ok and new_name.strip():
            new_name = new_name.strip()
            # Проверка на уникальность имени
            for mid, mdata in macros.items():
                if mid != macro_id and mdata.get('name') == new_name:
                    QMessageBox.warning(self, "Помилка", "Макрос з таким ім'ям вже існує.")
                    return
            # Переименовываем
            macros[macro_id]['name'] = new_name
            self.update_macros_list()
            # Обновляем отображение в узлах MacroNode
            self._update_all_items_properties()
            # Обновляем заголовок, если этот макрос редактируется
            if self.active_macro_id == macro_id:
                self._update_window_title()


    def on_scenario_item_double_clicked(self, item):
        # Если мы в режиме макроса, возвращаемся
        if self.current_edit_mode == EDIT_MODE_MACRO:
             if not self.return_to_scenario(): # Если возврат не удался (например, отменен)
                  return
        # Теперь позволяем редактировать имя сценария
        self._old_scenario_name = item.text()
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        self.scenarios_list.editItem(item)

    def on_macro_item_double_clicked(self, item):
        macro_id = item.data(Qt.ItemDataRole.UserRole)
        if macro_id:
            self.edit_macro(macro_id)

    def on_scenario_renamed(self, item):
        new_name = item.text().strip()
        old_name = self._old_scenario_name
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable) # Снимаем флаг редактирования

        if not new_name or not old_name or new_name == old_name:
            if old_name: item.setText(old_name) # Возвращаем старое имя, если новое пустое или не изменилось
            self._old_scenario_name = None
            return

        if new_name in self.project_data['scenarios']:
            QMessageBox.warning(self, "Помилка перейменування", "Сценарій з такою назвою вже існує.")
            item.setText(old_name) # Возвращаем старое имя
            self._old_scenario_name = None
            return

        self.project_data['scenarios'][new_name] = self.project_data['scenarios'].pop(old_name)
        if self.active_scenario_id == old_name:
            self.active_scenario_id = new_name
            self._update_window_title() # Обновляем заголовок

        self._old_scenario_name = None


    def on_active_scenario_changed(self, current_item, previous_item):
        # Этот слот вызывается ТОЛЬКО для списка сценариев
        # Игнорируем, если мы переключаемся из режима макроса
        if self.current_edit_mode == EDIT_MODE_MACRO:
            # Восстанавливаем выбор сценария, если он был
            if self.previous_scenario_id:
                 items = self.scenarios_list.findItems(self.previous_scenario_id, Qt.MatchFlag.MatchExactly)
                 if items:
                      self.scenarios_list.blockSignals(True)
                      self.scenarios_list.setCurrentItem(items[0])
                      self.scenarios_list.blockSignals(False)
            return

        # Нормальная обработка смены сценария
        if self.simulator.is_running:
            self.stop_simulation()
        if previous_item:
            prev_id = previous_item.text()
            if prev_id in self.project_data['scenarios']: self.save_current_scenario_state()
        if current_item:
            self.active_scenario_id = current_item.text()
            self.load_scenario_state(self.active_scenario_id)
        else:
            self.active_scenario_id = None
            self.scene.clear()
        self.undo_stack.clear()
        self.current_selected_node = None
        self.on_selection_changed()
        self._update_window_title()

    def set_edit_mode(self, mode):
        if self.current_edit_mode == mode: return
        log.info(f"Switching edit mode to {'MACRO' if mode == EDIT_MODE_MACRO else 'SCENARIO'}")
        self.current_edit_mode = mode
        self.undo_stack.clear()
        self._update_toolbars()
        self._update_window_title()
        self._update_nodes_list()
        # Обновляем доступность панели симуляции
        self.sim_toolbar.setVisible(mode == EDIT_MODE_SCENARIO)
        # Сбрасываем выделение при смене режима
        self.scene.clearSelection()
        self.current_selected_node = None
        self.on_selection_changed()


    def edit_macro(self, macro_id):
        if self.current_edit_mode == EDIT_MODE_MACRO and self.active_macro_id == macro_id:
            return # Уже редактируем этот макрос

        if macro_id not in self.project_data.get('macros', {}):
             log.error(f"Cannot edit macro: ID {macro_id} not found.")
             return

        # Сохраняем текущее состояние (сценария)
        self.save_current_state()
        self.previous_scenario_id = self.active_scenario_id # Запоминаем сценарий

        self.active_scenario_id = None
        self.active_macro_id = macro_id
        self.set_edit_mode(EDIT_MODE_MACRO)
        self.load_macro_state(macro_id)
        # Переключаем вкладку проекта на макросы
        self.project_tabs.setCurrentIndex(1)
        # Выделяем макрос в списке
        items = self.macros_list.findItems(self.project_data['macros'][macro_id]['name'], Qt.MatchFlag.MatchExactly)
        if items:
            self.macros_list.blockSignals(True)
            self.macros_list.setCurrentItem(items[0])
            self.macros_list.blockSignals(False)


    def return_to_scenario(self, force_return=False):
        if self.current_edit_mode != EDIT_MODE_MACRO: return True

        # TODO: Проверить на несохраненные изменения? Или сохранять автоматически?
        # Пока сохраняем автоматически.
        self.save_current_state()

        scenario_to_load = self.previous_scenario_id
        self.active_macro_id = None
        self.previous_scenario_id = None
        self.set_edit_mode(EDIT_MODE_SCENARIO)

        if scenario_to_load and scenario_to_load in self.project_data['scenarios']:
            self.active_scenario_id = scenario_to_load
            self.load_scenario_state(scenario_to_load)
            # Выделяем сценарий в списке
            items = self.scenarios_list.findItems(scenario_to_load, Qt.MatchFlag.MatchExactly)
            if items:
                self.scenarios_list.blockSignals(True)
                self.scenarios_list.setCurrentItem(items[0])
                self.scenarios_list.blockSignals(False)
        else:
            # Если нет предыдущего сценария (или он был удален),
            # загружаем первый попавшийся или очищаем сцену
            if self.scenarios_list.count() > 0:
                self.scenarios_list.setCurrentRow(0) # Загрузит первый сценарий
            else:
                self.active_scenario_id = None
                self.scene.clear()
                self._update_window_title()

        # Переключаем вкладку проекта на сценарии
        self.project_tabs.setCurrentIndex(0)
        return True


    def save_current_state(self):
        """Сохраняет состояние текущего редактируемого элемента (сценария или макроса)."""
        if self.current_edit_mode == EDIT_MODE_SCENARIO:
            self.save_current_scenario_state()
        elif self.current_edit_mode == EDIT_MODE_MACRO:
            self.save_macro_state()


    def save_current_scenario_state(self):
        if not self.active_scenario_id or self.active_scenario_id not in self.project_data['scenarios']: return
        log.debug(f"Saving state for scenario: {self.active_scenario_id}")
        nodes_data, connections_data, comments_data, frames_data = [], [], [], []
        for item in self.scene.items():
            if hasattr(item, 'to_data'):
                data = item.to_data()
                if not data: continue
                if isinstance(item, BaseNode): nodes_data.append(data)
                elif isinstance(item, Connection): connections_data.append(data)
                elif isinstance(item, CommentItem): comments_data.append(data)
                elif isinstance(item, FrameItem): frames_data.append(data)
        self.project_data['scenarios'][self.active_scenario_id] = {
            'nodes': nodes_data,
            'connections': connections_data,
            'comments': comments_data,
            'frames': frames_data
        }

    def save_macro_state(self):
        """Сохраняет состояние текущего редактируемого макроса."""
        if not self.active_macro_id or self.active_macro_id not in self.project_data.get('macros', {}): return
        log.debug(f"Saving state for macro: {self.active_macro_id}")

        macro_data = self.project_data['macros'][self.active_macro_id]
        nodes_data, connections_data = [], []
        inputs_list = []
        outputs_list = []

        for item in self.scene.items():
            if hasattr(item, 'to_data'):
                data = item.to_data()
                if not data: continue
                if isinstance(item, MacroInputNode):
                    inputs_list.append({
                        'name': item.node_name, # Имя берем из узла
                        # 'internal_node_id': '', # Не используется при сохранении
                        # 'internal_socket_name': '',
                        'macro_input_node_id': item.id # Сохраняем ID узла входа
                    })
                    nodes_data.append(data) # Сам узел тоже сохраняем
                elif isinstance(item, MacroOutputNode):
                    outputs_list.append({
                        'name': item.node_name,
                        # 'internal_node_id': '',
                        # 'internal_socket_name': '',
                        'macro_output_node_id': item.id
                    })
                    nodes_data.append(data)
                elif isinstance(item, BaseNode): # Остальные узлы
                    nodes_data.append(data)
                elif isinstance(item, Connection):
                    connections_data.append(data)
                # Комментарии и фреймы пока не сохраняем в макросах

        # Обновляем данные макроса
        macro_data['nodes'] = nodes_data
        macro_data['connections'] = connections_data
        macro_data['inputs'] = inputs_list
        macro_data['outputs'] = outputs_list
        # Имя макроса хранится отдельно, не здесь

        # После сохранения макроса нужно обновить узлы MacroNode в сценариях,
        # которые его используют, т.к. могли измениться входы/выходы.
        self.update_macro_nodes_in_scenarios(self.active_macro_id)


    def load_macro_state(self, macro_id):
        """Загружает состояние макроса в редактор."""
        self.scene.clear()
        macro_data = self.project_data.get('macros', {}).get(macro_id)
        if not macro_data:
             log.error(f"Cannot load macro state: ID {macro_id} not found.")
             return

        log.debug(f"Loading state for macro: {macro_id}")
        nodes_map = {}
        for node_data in macro_data.get('nodes', []):
            try:
                node = BaseNode.from_data(node_data)
                # Макро-узлы не должны быть внутри макроса, но на всякий случай
                if isinstance(node, MacroNode):
                    log.warning(f"Found MacroNode inside macro definition {macro_id}. This is not allowed.")
                    continue
                self.scene.addItem(node);
                nodes_map[node.id] = node
            except Exception as e:
                log.error(f"Failed to load node from macro data {node_data}: {e}", exc_info=True)


        for conn_data in macro_data.get('connections', []):
            start_node = nodes_map.get(conn_data['from_node'])
            end_node = nodes_map.get(conn_data['to_node'])
            if start_node and end_node:
                start_socket = start_node.get_socket(conn_data.get('from_socket', 'out'))
                end_socket = end_node.get_socket(conn_data.get('to_socket', 'in'))
                if start_socket and end_socket:
                    self.scene.addItem(Connection(start_socket, end_socket))
                else:
                    log.warning(f"Could not create macro connection, socket not found: {conn_data}")
            else:
                 log.warning(f"Could not create macro connection, node not found: {conn_data}")

        # Комментарии/фреймы пока не загружаем для макросов

        QTimer.singleShot(1, self._update_all_items_properties) # Обновляем отображение
        # Валидация для макроса может быть другой
        # self.validate_macro()


    def update_macro_nodes_in_scenarios(self, updated_macro_id):
        """Обновляет сокеты всех узлов MacroNode, ссылающихся на измененный макрос."""
        log.debug(f"Updating MacroNodes in scenarios for macro {updated_macro_id}")
        macro_data = self.project_data.get('macros', {}).get(updated_macro_id)
        if not macro_data: return

        # Нужно пройти по ВСЕМ сценариям
        for scenario_id, scenario_data in self.project_data.get('scenarios', {}).items():
            # Если сценарий сейчас загружен на сцену, обновляем прямо на сцене
            if scenario_id == self.active_scenario_id and self.current_edit_mode == EDIT_MODE_SCENARIO:
                 for item in self.scene.items():
                      if isinstance(item, MacroNode) and item.macro_id == updated_macro_id:
                           log.debug(f"Updating sockets for MacroNode {item.id} on current scene.")
                           item.update_sockets_from_definition(macro_data)
                           # TODO: Нужно перепроверить/удалить/восстановить соединения!
            else:
                # Если сценарий не загружен, нужно обновить его данные в project_data
                # Это сложнее, т.к. нужно найти узел в данных и обновить его сокеты
                # Пока пропустим этот шаг, он потребует рефакторинга хранения данных
                # log.warning(f"Updating MacroNodes in unloaded scenario data ({scenario_id}) is not implemented yet.")
                pass


    # --- (Метод load_scenario_state остается прежним) ---
    def load_scenario_state(self, scenario_id):
        self.scene.clear()
        scenario_data = self.project_data['scenarios'].get(scenario_id, {})
        nodes_map = {}
        for node_data in scenario_data.get('nodes', []):
            try:
                node = BaseNode.from_data(node_data)
                # Если это MacroNode, нужно обновить его сокеты
                if isinstance(node, MacroNode) and node.macro_id:
                    macro_def = self.project_data.get('macros', {}).get(node.macro_id)
                    if macro_def:
                        node.update_sockets_from_definition(macro_def)
                self.scene.addItem(node);
                nodes_map[node.id] = node
            except Exception as e:
                log.error(f"Failed to load node from data {node_data}: {e}", exc_info=True)


        for conn_data in scenario_data.get('connections', []):
            start_node = nodes_map.get(conn_data['from_node'])
            end_node = nodes_map.get(conn_data['to_node'])
            if start_node and end_node:
                start_socket = start_node.get_socket(conn_data.get('from_socket', 'out'))
                end_socket = end_node.get_socket(conn_data.get('to_socket', 'in'))
                if start_socket and end_socket:
                    self.scene.addItem(Connection(start_socket, end_socket))
                else:
                    log.warning(f"Could not create connection, socket not found for data: {conn_data}")
            else:
                log.warning(f"Could not create connection, node not found for data: {conn_data}")

        for comment_data in scenario_data.get('comments', []):
            self.scene.addItem(CommentItem.from_data(comment_data, self.view))
        for frame_data in scenario_data.get('frames', []):
            self.scene.addItem(FrameItem.from_data(frame_data, self.view))

        QTimer.singleShot(1, self._update_all_items_properties)
        self._update_simulation_trigger_zones()


    def _update_all_items_properties(self):
        """Обновляет отображаемые свойства для всех узлов на сцене."""
        for item in self.scene.items():
            if isinstance(item, BaseNode):
                item.update_display_properties(self.project_data.get('config'))
        self.validate_current_view() # Используем новый метод валидации

    # --- (Методы update_scenarios_list, update_macros_list остаются прежними) ---
    def update_scenarios_list(self):
        current_text = self.scenarios_list.currentItem().text() if self.scenarios_list.currentItem() else self.active_scenario_id
        self.scenarios_list.blockSignals(True)
        self.scenarios_list.clear()
        for name in sorted(self.project_data.get('scenarios', {}).keys()):
            item = QListWidgetItem(name)
            self.scenarios_list.addItem(item)
        if current_text:
            items = self.scenarios_list.findItems(current_text, Qt.MatchFlag.MatchExactly)
            if items: self.scenarios_list.setCurrentItem(items[0])
        self.scenarios_list.blockSignals(False)

    def update_macros_list(self):
        """Обновляет список макросов в UI."""
        current_id = None
        current_item = self.macros_list.currentItem()
        if current_item:
            current_id = current_item.data(Qt.ItemDataRole.UserRole)

        self.macros_list.blockSignals(True)
        self.macros_list.clear()
        macros = self.project_data.get('macros', {})
        new_item_to_select = None
        for macro_id, macro_data in sorted(macros.items(), key=lambda item: item[1].get('name', '')):
            item = QListWidgetItem(macro_data.get('name', macro_id))
            item.setData(Qt.ItemDataRole.UserRole, macro_id) # Сохраняем ID в данных элемента
            self.macros_list.addItem(item)
            if macro_id == current_id:
                new_item_to_select = item

        if new_item_to_select:
             self.macros_list.setCurrentItem(new_item_to_select)

        self.macros_list.blockSignals(False)
        # Также обновить выпадающий список в свойствах, если он открыт
        if self.current_selected_node and isinstance(self.current_selected_node, MacroNode):
            self._update_macro_props_ui()

    def validate_current_view(self):
        """Вызывает валидацию для текущего режима (сценарий или макрос)."""
        if self.current_edit_mode == EDIT_MODE_SCENARIO:
            self.validate_scenario()
        elif self.current_edit_mode == EDIT_MODE_MACRO:
            self.validate_macro()


    def validate_scenario(self):
        QTimer.singleShot(1, self._perform_scenario_validation)

    def _perform_scenario_validation(self):
        if not self.scene or self.current_edit_mode != EDIT_MODE_SCENARIO: return

        all_nodes = []
        trigger_node = None
        config = self.project_data.get('config')

        for item in self.scene.items():
            if isinstance(item, BaseNode):
                all_nodes.append(item)
                item.validate(config) # Базовая валидация узла
                if isinstance(item, TriggerNode):
                    trigger_node = item

        # Проверка наличия триггера
        if not trigger_node:
            for node in all_nodes:
                if not isinstance(node, TriggerNode) and not node.error_icon.isVisible():
                    node.set_validation_state(False, "В сценарії відсутній тригер.")
            return # Дальнейшие проверки не имеют смысла без триггера
        elif trigger_node.error_icon.isVisible(): # Если триггер невалиден сам по себе
             return # Остальные проверки могут быть некорректны

        # Проверка достижимости от триггера
        q = [trigger_node]
        reachable_nodes = {trigger_node}
        while q:
            current_node = q.pop(0)
            for socket in current_node.get_output_sockets():
                for conn in socket.connections:
                    next_node = conn.end_socket.parentItem()
                    if isinstance(next_node, BaseNode) and next_node not in reachable_nodes:
                        reachable_nodes.add(next_node)
                        q.append(next_node)

        TERMINAL_NODE_TYPES = (ActivateOutputNode, DeactivateOutputNode, SendSMSNode) # MacroOutput не терминальный в сценарии

        for node in all_nodes:
            # Сбрасываем старую ошибку о недостижимости/зависании, если она больше не актуальна
            current_tooltip = node.error_icon.toolTip()
            reset_error = False
            if node in reachable_nodes and current_tooltip == "Вузол недосяжний від тригера.":
                 reset_error = True
            if node not in reachable_nodes and current_tooltip == "Ланцюжок логіки не завершено дією.":
                 # Ошибка о недостижимости важнее
                 pass # Не сбрасываем, она перекроется ниже
            elif current_tooltip == "Ланцюжок логіки не завершено дією.":
                 # Перепроверяем, может узел стал терминальным или к нему что-то подключили
                 is_terminal = isinstance(node, TERMINAL_NODE_TYPES)
                 has_outputs = any(sock.connections for sock in node.get_output_sockets())
                 if is_terminal or has_outputs:
                      reset_error = True

            if reset_error and not node.error_icon.isVisible(): # Сбрасываем, только если НЕТ ДРУГИХ ошибок
                 node.set_validation_state(True)


            # Проверяем на недостижимость
            if node not in reachable_nodes:
                node.set_validation_state(False, "Вузол недосяжний від тригера.")
            # Если ошибок еще нет, проверяем на "висячие" выходы
            elif not node.error_icon.isVisible():
                is_terminal = isinstance(node, TERMINAL_NODE_TYPES)
                # Для всех узлов, кроме терминальных, должен быть хотя бы один выход
                # Исключаем MacroNode, т.к. его выходы могут быть не подключены, если они не используются
                if not is_terminal and not isinstance(node, MacroNode) and not any(sock.connections for sock in node.get_output_sockets()):
                    node.set_validation_state(False, "Ланцюжок логіки не завершено дією.")

    def validate_macro(self):
        """Выполняет валидацию для текущего редактируемого макроса."""
        QTimer.singleShot(1, self._perform_macro_validation)

    def _perform_macro_validation(self):
        if not self.scene or self.current_edit_mode != EDIT_MODE_MACRO: return
        log.debug(f"Performing validation for macro {self.active_macro_id}")

        all_nodes = []
        input_nodes = []
        output_nodes = []
        config = self.project_data.get('config') # Конфиг может быть нужен для некоторых узлов

        for item in self.scene.items():
            if isinstance(item, BaseNode):
                all_nodes.append(item)
                item.validate(config) # Базовая валидация узла
                if isinstance(item, MacroInputNode):
                    input_nodes.append(item)
                elif isinstance(item, MacroOutputNode):
                    output_nodes.append(item)

        # Проверка: Должен быть хотя бы один вход и один выход? (Опционально)
        if not input_nodes:
             # Можно выводить предупреждение, а не ошибку
             # self.show_status_message("Попередження: Макрос не має вузла 'Вхід Макроса'.", 5000, color="orange")
             pass
        if not output_nodes:
             # self.show_status_message("Попередження: Макрос не має вузла 'Вихід Макроса'.", 5000, color="orange")
             pass

        # Проверка достижимости всех выходов от всех входов
        # Проверка, что все пути заканчиваются на выходе макроса
        # Это сложнее, требует обхода графа

        # Проверка уникальности имен входов/выходов (если разрешено несколько)
        input_names = [n.node_name for n in input_nodes]
        output_names = [n.node_name for n in output_nodes]
        if len(input_names) != len(set(input_names)):
            # Найти дубликаты и подсветить узлы
            log.error("Macro validation failed: Duplicate MacroInputNode names.")
            # TODO: Add visual indication for duplicate nodes
        if len(output_names) != len(set(output_names)):
             log.error("Macro validation failed: Duplicate MacroOutputNode names.")
             # TODO: Add visual indication

        # Пример: проверка, что у входа есть выходное соединение
        for inp_node in input_nodes:
             if not inp_node.out_socket or not inp_node.out_socket.connections:
                  if not inp_node.error_icon.isVisible(): # Не перезаписываем другие ошибки
                       inp_node.set_validation_state(False, f"Вузол '{inp_node.node_name}' нікуди не підключено.")

        # Пример: проверка, что у выхода есть входное соединение
        for outp_node in output_nodes:
             if not outp_node.in_socket or not outp_node.in_socket.connections:
                  if not outp_node.error_icon.isVisible():
                       outp_node.set_validation_state(False, f"До вузла '{outp_node.node_name}' нічого не підключено.")

        log.debug("Macro validation finished.")


    # --- (Методы симуляции _update_simulation_trigger_zones, update_simulation_controls, start/step/stop, get_user_choice остаются) ---
    def _update_simulation_trigger_zones(self):
        # Симуляция доступна только в режиме сценария
        if self.current_edit_mode != EDIT_MODE_SCENARIO:
             self.sim_trigger_zone_combo.clear()
             self.sim_trigger_zone_combo.addItem("Недоступно в режимі макросу", userData=None)
             self.update_simulation_controls()
             return

        self.sim_trigger_zone_combo.clear()
        trigger_node = next((item for item in self.scene.items() if isinstance(item, TriggerNode)), None)

        if not trigger_node:
            self.sim_trigger_zone_combo.addItem("Тригер не знайдено", userData=None)
            self.update_simulation_controls()
            return

        props = dict(trigger_node.properties)
        zone_ids = props.get('zones', [])

        if not zone_ids:
            self.sim_trigger_zone_combo.addItem("Немає зон в тригері", userData=None)
        else:
            all_zones, _ = self._get_all_zones_and_outputs_from_devices()
            found_zones = False
            for zid in zone_ids:
                for z in all_zones:
                    if z['id'] == zid:
                        self.sim_trigger_zone_combo.addItem(f"{z['parent_name']}: {z['name']}", userData=zid)
                        found_zones = True
                        break
            if not found_zones:
                self.sim_trigger_zone_combo.addItem("Призначені зони не знайдено", userData=None)

        self.update_simulation_controls()

    def update_simulation_controls(self):
        # Симуляция доступна только в режиме сценария
        sim_enabled = self.current_edit_mode == EDIT_MODE_SCENARIO
        is_ready_for_sim = False

        if sim_enabled and self.scene:
            trigger_node = next((item for item in self.scene.items() if isinstance(item, TriggerNode)), None)
            # Готов к симуляции, если есть валидный триггер и выбрана зона
            if trigger_node and not trigger_node.error_icon.isVisible():
                is_ready_for_sim = self.sim_trigger_zone_combo.count() > 0 and self.sim_trigger_zone_combo.currentData() is not None

        is_running = self.simulator.is_running
        self.start_sim_action.setEnabled(sim_enabled and is_ready_for_sim and not is_running)
        self.step_sim_action.setEnabled(sim_enabled and is_running)
        self.stop_sim_action.setEnabled(sim_enabled and is_running)
        self.sim_trigger_zone_combo.setEnabled(sim_enabled and not is_running)

    def start_simulation(self):
        if self.current_edit_mode != EDIT_MODE_SCENARIO: return
        self.validate_scenario() # Валидируем именно сценарий
        for item in self.scene.items():
            if isinstance(item, BaseNode) and item.error_icon.isVisible():
                self.show_status_message("Помилка: Неможливо почати симуляцію, у сценарії є помилки.", 5000,
                                         color="red")
                return

        trigger_zone_id = self.sim_trigger_zone_combo.currentData()
        if self.simulator.start(trigger_zone_id):
            self.view.set_interactive(False)
            self.update_simulation_controls()

    def step_simulation(self):
        if self.current_edit_mode != EDIT_MODE_SCENARIO: return
        self.simulator.step()
        if not self.simulator.is_running:
            self.show_status_message("Симуляція завершена.", color="lime")
            self.stop_simulation()
        else:
            self.update_simulation_controls()

    def stop_simulation(self):
        # Может быть вызвана из симулятора, когда мы в режиме сценария
        self.simulator.stop()
        self.view.set_interactive(True) # Включаем интерактивность всегда при стопе
        self.update_simulation_controls()

    def get_user_choice_for_condition(self, node):
        props = dict(node.properties)
        zone_id = props.get('zone_id')
        zone_name = "Невідома зона"

        all_zones, _ = self._get_all_zones_and_outputs_from_devices()
        for z in all_zones:
            if z['id'] == zone_id:
                zone_name = f"'{z['parent_name']}: {z['name']}'"
                break

        items = ["Під охороною", "Знята з охорони", "Тривога"]
        item, ok = QInputDialog.getItem(self, "Симуляція: Вузол 'Умова'",
                                        f"Який поточний стан зони {zone_name}?",
                                        items, 0, False)
        if ok and item:
            return item
        return None


    def show_status_message(self, message, timeout=4000, color=None):
        style = f"color: {color};" if color else ""
        self.statusBar().setStyleSheet(style)
        self.statusBar().showMessage(message, timeout)
        # Сбрасываем стиль после таймаута
        if color: QTimer.singleShot(timeout, lambda: self.statusBar().setStyleSheet(""))

    # --- (Методы copy/paste/import/export требуют доработки для макросов) ---
    def copy_selection(self):
        # Копирование пока работает только для текущего вида (сценария или макроса)
        # Не копируем MacroInput/Output
        selected_items = self.scene.selectedItems()
        nodes_to_copy = [item for item in selected_items if isinstance(item, BaseNode) and not isinstance(item, (MacroInputNode, MacroOutputNode))]
        comments_to_copy = [item for item in selected_items if isinstance(item, CommentItem)]
        frames_to_copy = [item for item in selected_items if isinstance(item, FrameItem)]

        if not (nodes_to_copy or comments_to_copy or frames_to_copy): return

        clipboard_root = ET.Element("clipboard_data")
        nodes_xml = ET.SubElement(clipboard_root, "nodes")
        connections_xml = ET.SubElement(clipboard_root, "connections")
        comments_xml = ET.SubElement(clipboard_root, "comments")
        frames_xml = ET.SubElement(clipboard_root, "frames")

        node_ids_to_copy = {node.id for node in nodes_to_copy}

        for node in nodes_to_copy: node.to_xml(nodes_xml)
        for comment in comments_to_copy: comment.to_xml(comments_xml)
        for frame in frames_to_copy: frame.to_xml(frames_xml)


        # Копируем только те соединения, оба конца которых находятся в выделении
        for item in self.scene.items():
            if isinstance(item, Connection):
                start_node = item.start_socket.parentItem()
                end_node = item.end_socket.parentItem()
                # Проверяем, что оба узла существуют и их ID в списке копируемых
                if start_node and end_node and start_node.id in node_ids_to_copy and end_node.id in node_ids_to_copy:
                    item.to_xml(connections_xml)

        clipboard_string = ET.tostring(clipboard_root, pretty_print=True, encoding="unicode")
        QApplication.clipboard().setText(clipboard_string)
        self.show_status_message(f"Скопійовано {len(nodes_to_copy) + len(comments_to_copy) + len(frames_to_copy)} елемент(и).")


    def paste_at_center(self):
        self.paste_selection()

    def paste_selection(self, view_pos=None):
        # --- ИСПРАВЛЕНО: Импортируем PasteCommand здесь ---
        from commands import PasteCommand
        # Вставка работает только в текущий контекст
        # Нельзя вставить Trigger в макрос, MacroInput/Output в сценарий и т.д.
        # Команда PasteCommand должна будет это проверить.
        clipboard_string = QApplication.clipboard().text()
        if not clipboard_string: return
        paste_pos = self.view.mapToScene(view_pos or self.view.viewport().rect().center())
        # Передаем текущий режим в команду
        command = PasteCommand(self.scene, clipboard_string, paste_pos, self.view, self.current_edit_mode)
        self.undo_stack.push(command)

    def _load_project_data_from_file(self, path):
        log.debug(f"Attempting to load project from: {path}")
        try:
            root_xml = ET.parse(path).getroot()
            new_project_data = {
                'scenarios': {},
                'macros': {},
                'config': {'devices': [], 'users': []}
            }

            # Config
            config_xml = root_xml.find("config")
            if config_xml is not None:
                log.debug("Parsing <config> section...")
                devices_xml = config_xml.find("devices")
                if devices_xml is not None:
                    log.debug("Parsing <devices>...")
                    for device_el in devices_xml:
                        device_id = device_el.get('id')
                        log.debug(f"Parsing device ID: {device_id}")
                        device_data = {'id': device_id, 'name': device_el.get('name'),
                                       'type': device_el.get('type'), 'zones': [], 'outputs': []}
                        zones_xml = device_el.find('zones')
                        if zones_xml is not None:
                            for zone_el in zones_xml:
                                log.debug(f"  - Parsing zone ID: {zone_el.get('id')}")
                                device_data['zones'].append(
                                    {'id': zone_el.get('id'), 'name': zone_el.get('name'),
                                     'parent_name': device_data['name']})
                        outputs_xml = device_el.find('outputs')
                        if outputs_xml is not None:
                            for output_el in outputs_xml:
                                log.debug(f"  - Parsing output ID: {output_el.get('id')}")
                                device_data['outputs'].append(
                                    {'id': output_el.get('id'), 'name': output_el.get('name'),
                                     'parent_name': device_data['name']})
                        new_project_data['config']['devices'].append(device_data)

                users_xml = config_xml.find("users")
                if users_xml is not None:
                    log.debug("Parsing <users>...")
                    for user_el in users_xml:
                        log.debug(f"Parsing user ID: {user_el.get('id')}")
                        new_project_data['config']['users'].append(
                            {'id': user_el.get("id"), 'name': user_el.get("name"), 'phone': user_el.get("phone")})


            # Scenarios
            scenarios_xml = root_xml.find("scenarios")
            if scenarios_xml is not None:
                 log.debug("Parsing <scenarios> section...")
                 for scenario_el in scenarios_xml:
                    scenario_id = scenario_el.get("id")
                    log.debug(f"Parsing scenario ID: {scenario_id}")
                    if not scenario_id: continue
                    nodes_data, connections_data, comments_data, frames_data = [], [], [], []

                    nodes_xml = scenario_el.find("nodes")
                    if nodes_xml is not None:
                        for node_el in nodes_xml:
                            log.debug(f"  - Parsing node ID: {node_el.get('id')}")
                            nodes_data.append(BaseNode.data_from_xml(node_el))

                    connections_xml = scenario_el.find("connections")
                    if connections_xml is not None:
                        for conn_el in connections_xml:
                            log.debug(f"  - Parsing connection from: {conn_el.get('from_node')} to: {conn_el.get('to_node')}")
                            connections_data.append(Connection.data_from_xml(conn_el))

                    comments_xml = scenario_el.find("comments")
                    if comments_xml is not None:
                        for comment_el in comments_xml:
                            log.debug(f"  - Parsing comment ID: {comment_el.get('id')}")
                            comments_data.append(CommentItem.data_from_xml(comment_el))

                    frames_xml = scenario_el.find("frames")
                    if frames_xml is not None:
                         for frame_el in frames_xml:
                              log.debug(f"  - Parsing frame ID: {frame_el.get('id')}")
                              frames_data.append(FrameItem.data_from_xml(frame_el))

                    new_project_data['scenarios'][scenario_id] = {'nodes': nodes_data, 'connections': connections_data,
                                                                  'comments': comments_data, 'frames': frames_data}


            # Macros
            macros_xml = root_xml.find("macros")
            if macros_xml is not None:
                 log.debug("Parsing <macros> section...")
                 for macro_el in macros_xml:
                    macro_id = macro_el.get("id")
                    log.debug(f"Parsing macro ID: {macro_id}")
                    if not macro_id: continue
                    macro_data = {
                        'id': macro_id,
                        'name': macro_el.get('name'),
                        'nodes': [], 'connections': [], 'inputs': [], 'outputs': []
                    }
                    nodes_xml = macro_el.find("nodes")
                    if nodes_xml is not None:
                         for node_el in nodes_xml: macro_data['nodes'].append(BaseNode.data_from_xml(node_el))
                    connections_xml = macro_el.find("connections")
                    if connections_xml is not None:
                         for conn_el in connections_xml: macro_data['connections'].append(Connection.data_from_xml(conn_el))

                    # Parse inputs/outputs definitions
                    inputs_xml = macro_el.find("inputs")
                    if inputs_xml is not None:
                         for input_el in inputs_xml:
                              macro_data['inputs'].append({
                                   'name': input_el.get('name'),
                                   'macro_input_node_id': input_el.get('node_id') # Ссылка на ID узла MacroInputNode внутри макроса
                                   # 'internal_node_id' и 'internal_socket_name' здесь не нужны
                              })
                    outputs_xml = macro_el.find("outputs")
                    if outputs_xml is not None:
                         for output_el in outputs_xml:
                              macro_data['outputs'].append({
                                   'name': output_el.get('name'),
                                   'macro_output_node_id': output_el.get('node_id')
                              })

                    new_project_data['macros'][macro_id] = macro_data

            log.debug("Successfully finished parsing XML file.")
            return new_project_data
        except Exception as e:
            log.critical(f"Critical error while parsing XML file: {e}", exc_info=True)
            QMessageBox.critical(self, "Помилка читання файлу", f"Не вдалося прочитати дані з файлу:\n{e}")
            return None


    def import_project(self):
        path, _ = QFileDialog.getOpenFileName(self, "Імпорт проекту", "", "XML Files (*.xml)")
        if not path: return

        log.info(f"Starting project import from: {path}")
        try:
            new_project_data = self._load_project_data_from_file(path)
            if new_project_data is None:
                log.error("Failed to load project data, _load_project_data_from_file returned None.")
                return

            self.project_data = new_project_data
            self.scene.clear()
            self.undo_stack.clear()
            self.scenarios_list.blockSignals(True)
            self.macros_list.blockSignals(True)
            self.active_scenario_id = None
            self.active_macro_id = None
            self.current_edit_mode = EDIT_MODE_SCENARIO # Начинаем со сценариев
            self.previous_scenario_id = None
            self.current_selected_node = None

            log.debug("Updating UI after import...")
            self.update_config_ui()
            self.update_scenarios_list()
            self.update_macros_list()
            self.props_widget.setEnabled(False)
            self._update_toolbars()
            self._update_nodes_list()

            scenario_keys = sorted(self.project_data.get('scenarios', {}).keys())
            if scenario_keys:
                first_scenario_id = scenario_keys[0]
                log.debug(f"Setting first scenario active: {first_scenario_id}")
                items = self.scenarios_list.findItems(first_scenario_id, Qt.MatchFlag.MatchExactly)
                if items:
                     self.scenarios_list.setCurrentItem(items[0])
                     # Загрузка будет вызвана сигналом currentItemChanged
            else:
                 self.active_scenario_id = None
                 self.scene.clear() # Очищаем сцену, если нет сценариев
                 self._update_window_title()


            self.scenarios_list.blockSignals(False)
            self.macros_list.blockSignals(False)

            # Переключаем вкладку на сценарии
            self.project_tabs.setCurrentIndex(0)

            # Ручной вызов on_active_scenario_changed нужен только если сценарий уже выбран,
            # но сигнал не сработал (например, если он был первым и единственным)
            # if self.scenarios_list.currentItem() and not self.active_scenario_id:
            #      self.on_active_scenario_changed(self.scenarios_list.currentItem(), None)


            self.show_status_message(f"Проект успішно імпортовано з {path}", color="green")
            log.info("Project imported successfully.")

        except Exception as e:
            log.critical(f"An unhandled exception occurred during project import: {e}", exc_info=True)
            QMessageBox.critical(self, "Помилка імпорту", f"Не вдалося імпортувати проект:\n{e}")
            self.new_project() # Сбрасываем к новому проекту при ошибке

    def export_project(self):
        self.save_current_state() # Сохраняем текущий вид (сценарий или макрос)
        path, _ = QFileDialog.getSaveFileName(self, "Експорт проекту", "", "XML Files (*.xml)")
        if not path: return
        try:
            root_xml = ET.Element("project")

            # Config saving
            config_xml = ET.SubElement(root_xml, "config")
            devices_xml = ET.SubElement(config_xml, "devices")
            for device in self.project_data['config'].get('devices', []):
                device_el = ET.SubElement(devices_xml, "device", id=device['id'], name=device['name'], type=device['type'])
                zones_xml = ET.SubElement(device_el, 'zones')
                outputs_xml = ET.SubElement(device_el, 'outputs')
                for zone in device.get('zones', []): ET.SubElement(zones_xml, 'zone', id=zone['id'], name=zone['name'])
                for output in device.get('outputs', []): ET.SubElement(outputs_xml, 'output', id=output['id'], name=output['name'])
            users_xml = ET.SubElement(config_xml, "users")
            for user in self.project_data['config'].get('users', []): ET.SubElement(users_xml, "user", id=user['id'], name=user['name'], phone=user.get('phone', ''))

            # Scenarios saving
            scenarios_xml = ET.SubElement(root_xml, "scenarios")
            for scenario_id, scenario_data in self.project_data.get('scenarios', {}).items():
                scenario_el = ET.SubElement(scenarios_xml, "scenario", id=scenario_id)
                nodes_el = ET.SubElement(scenario_el, "nodes")
                conns_el = ET.SubElement(scenario_el, "connections")
                comms_el = ET.SubElement(scenario_el, "comments")
                frames_el = ET.SubElement(scenario_el, "frames")
                for node_data in scenario_data.get('nodes', []): BaseNode.data_to_xml(nodes_el, node_data)
                for conn_data in scenario_data.get('connections', []): Connection.data_to_xml(conns_el, conn_data)
                for comm_data in scenario_data.get('comments', []): CommentItem.data_to_xml(comms_el, comm_data)
                for frame_data in scenario_data.get('frames', []): FrameItem.data_to_xml(frames_el, frame_data)

            # Macros saving
            macros_xml = ET.SubElement(root_xml, "macros")
            for macro_id, macro_data in self.project_data.get('macros', {}).items():
                macro_el = ET.SubElement(macros_xml, "macro", id=macro_id, name=macro_data.get('name', ''))
                nodes_el = ET.SubElement(macro_el, "nodes")
                conns_el = ET.SubElement(macro_el, "connections")
                inputs_el = ET.SubElement(macro_el, "inputs")
                outputs_el = ET.SubElement(macro_el, "outputs")
                for node_data in macro_data.get('nodes', []): BaseNode.data_to_xml(nodes_el, node_data)
                for conn_data in macro_data.get('connections', []): Connection.data_to_xml(conns_el, conn_data)
                for input_data in macro_data.get('inputs', []):
                     ET.SubElement(inputs_el, "input", name=input_data.get('name',''), node_id=input_data.get('macro_input_node_id',''))
                for output_data in macro_data.get('outputs', []):
                     ET.SubElement(outputs_el, "output", name=output_data.get('name',''), node_id=output_data.get('macro_output_node_id',''))

            tree = ET.ElementTree(root_xml);
            tree.write(path, pretty_print=True, xml_declaration=True, encoding="utf-8")
            self.show_status_message(f"Проект успішно експортовано до {path}", color="green")
        except Exception as e:
            log.error(f"Failed to export project: {e}", exc_info=True)
            QMessageBox.critical(self, "Помилка експорту", f"Не вдалося експортувати проект:\n{e}")

    def add_comment(self):
        # --- ИСПРАВЛЕНО: Импортируем AddCommentCommand здесь ---
        from commands import AddCommentCommand
        # Добавляем в текущий контекст
        if self.current_edit_mode == EDIT_MODE_SCENARIO and self.active_scenario_id is None: return
        if self.current_edit_mode == EDIT_MODE_MACRO and self.active_macro_id is None: return
        center_pos = self.view.mapToScene(self.view.viewport().rect().center())
        command = AddCommentCommand(self.scene, center_pos, self.view)
        self.undo_stack.push(command)

