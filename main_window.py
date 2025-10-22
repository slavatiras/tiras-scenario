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
    QScrollArea, QInputDialog
)
from PyQt6.QtGui import QColor, QAction, QUndoStack, QFont, QIcon
from PyQt6.QtCore import Qt, QTimer

from nodes import (BaseNode, Connection, CommentItem, NODE_REGISTRY, TriggerNode,
                   ActivateOutputNode, DeactivateOutputNode, DelayNode, SendSMSNode,
                   ConditionNodeZoneState, RepeatNode, SequenceNode)
from commands import (AddNodeCommand, AddCommentCommand, RemoveItemsCommand,
                      ChangePropertiesCommand, PasteCommand)
from editor_view import EditorView
from simulator import ScenarioSimulator

log = logging.getLogger(__name__)

DEVICE_SPECS = {
    "MOUT8R": {"type": "Модуль релейних виходів", "outputs": 8, "zones": 0},
    "PUIZ 2": {"type": "Пристрій індикації", "outputs": 0, "zones": 2},
    "ППКП Tiras-8L": {"type": "Базовий прилад", "outputs": 2, "zones": 8}
}


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Редактор сценаріїв Tiras");
        self.setGeometry(100, 100, 1400, 900)
        self.project_data = {}
        self.active_scenario_id = None;
        self.current_selected_node = None
        self._old_scenario_name = None
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
        self._create_tool_bar();
        self._create_simulation_toolbar()
        self._create_panels()
        self.scene.selectionChanged.connect(self.on_selection_changed)
        self.undo_stack.indexChanged.connect(lambda: QTimer.singleShot(1, self.validate_scenario))
        self.undo_stack.indexChanged.connect(self._update_simulation_trigger_zones)
        self.new_project()
        self.statusBar().showMessage("Готово")

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

    def _create_tool_bar(self):
        toolbar = QToolBar("Основна панель")
        self.addToolBar(toolbar)
        for node_type in sorted(NODE_REGISTRY.keys()):
            node_class = NODE_REGISTRY[node_type]
            icon = getattr(node_class, 'ICON', '●')
            action = QAction(icon, self)
            action.setToolTip(f"Додати вузол '{node_type}'")
            action.triggered.connect(lambda checked=False, nt=node_type: self._on_toolbar_action_triggered(nt))
            toolbar.addAction(action)

    def _create_simulation_toolbar(self):
        self.sim_toolbar = QToolBar("Панель симуляції")
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
        self.addToolBar(self.sim_toolbar)

    def _on_toolbar_action_triggered(self, node_type):
        self.add_node(node_type, self.view.mapToScene(self.view.viewport().rect().center()))

    def _create_panels(self):
        scenarios_dock = QDockWidget("Сценарії", self);
        scenarios_widget = QWidget();
        scenarios_layout = QVBoxLayout(scenarios_widget)
        self.scenarios_list = QListWidget();
        scenarios_layout.addWidget(self.scenarios_list)
        scenarios_btn_layout = QHBoxLayout();
        add_scenario_btn = QPushButton("Додати");
        remove_scenario_btn = QPushButton("Видалити")
        scenarios_btn_layout.addWidget(add_scenario_btn);
        scenarios_btn_layout.addWidget(remove_scenario_btn)
        scenarios_layout.addLayout(scenarios_btn_layout);
        scenarios_dock.setWidget(scenarios_widget)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, scenarios_dock)

        nodes_dock = QDockWidget("Елементи сценарію", self);
        self.nodes_list = QListWidget()
        self.nodes_list.addItems(sorted(list(NODE_REGISTRY.keys())));
        nodes_dock.setWidget(self.nodes_list)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, nodes_dock)

        config_dock = QDockWidget("Конфігурація системи", self);
        config_tabs = QTabWidget()

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

        add_scenario_btn.clicked.connect(lambda: self.add_scenario())
        remove_scenario_btn.clicked.connect(self.remove_scenario)
        self.scenarios_list.currentItemChanged.connect(self.on_active_scenario_changed)
        self.scenarios_list.itemDoubleClicked.connect(self.on_scenario_item_double_clicked)
        self.scenarios_list.itemChanged.connect(self.on_scenario_renamed)
        self.nodes_list.itemClicked.connect(self.on_node_list_clicked)
        add_device_btn.clicked.connect(self.add_device)
        remove_device_btn.clicked.connect(self.remove_device)
        add_user_btn.clicked.connect(lambda: self.add_config_item('users'))
        remove_user_btn.clicked.connect(lambda: self.remove_config_item('users'))
        self.devices_table.itemChanged.connect(self.on_config_table_changed)
        self.zones_table.itemChanged.connect(self.on_config_table_changed)
        self.outputs_table.itemChanged.connect(self.on_config_table_changed)
        self.users_table.itemChanged.connect(self.on_config_table_changed)

    def setup_properties_panel(self):
        while self.main_props_layout.count():
            child = self.main_props_layout.takeAt(0)
            if child.widget(): child.widget().deleteLater()

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

        self.main_props_layout.addStretch()

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

        if newly_selected_node:
            self.current_selected_node = newly_selected_node

        self.props_widget.setEnabled(newly_selected_node is not None)
        self._update_properties_panel_ui()

    def _update_properties_panel_ui(self):
        node = self.current_selected_node if self.props_widget.isEnabled() else None
        is_node_selected = node is not None

        self.base_props_widget.setVisible(True)
        self.trigger_props_widget.setVisible(False)
        self.output_props_widget.setVisible(False)
        self.delay_props_widget.setVisible(False)
        self.sms_props_widget.setVisible(False)
        self.condition_props_widget.setVisible(False)
        self.repeat_props_widget.setVisible(False)

        if is_node_selected:
            self.prop_name.blockSignals(True)
            self.prop_description.blockSignals(True)
            self.prop_name.setText(node.node_name)
            self.prop_description.setPlainText(node.description)
            self.prop_name.blockSignals(False)
            self.prop_description.blockSignals(False)

            if isinstance(node, TriggerNode):
                self._update_trigger_props_ui()
            elif isinstance(node, (ActivateOutputNode, DeactivateOutputNode)):
                self._update_output_props_ui()
            elif isinstance(node, DelayNode):
                self._update_delay_props_ui()
            elif isinstance(node, SendSMSNode):
                self._update_sms_props_ui()
            elif isinstance(node, ConditionNodeZoneState):
                self._update_condition_props_ui()
            elif isinstance(node, RepeatNode):
                self._update_repeat_props_ui()
        else:
            self.prop_name.clear()
            self.prop_description.clear()

    def _get_all_zones_and_outputs_from_devices(self):
        all_zones, all_outputs = [], []
        for device in self.project_data['config'].get('devices', []):
            all_zones.extend(device.get('zones', []))
            all_outputs.extend(device.get('outputs', []))
        return all_zones, all_outputs

    def _update_trigger_props_ui(self):
        node = self.current_selected_node
        self.trigger_props_widget.setVisible(True)
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
        self.output_props_widget.setVisible(True)
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
        self.delay_props_widget.setVisible(True)
        props = dict(node.properties)
        self.delay_spinbox.blockSignals(True)
        self.delay_spinbox.setValue(int(props.get('seconds', 0)))
        self.delay_spinbox.blockSignals(False)

    def _update_sms_props_ui(self):
        node = self.current_selected_node
        self.sms_props_widget.setVisible(True)
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
        self.condition_props_widget.setVisible(True)
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
        self.repeat_props_widget.setVisible(True)
        props = dict(node.properties)
        self.repeat_count_spinbox.blockSignals(True)
        self.repeat_count_spinbox.setValue(int(props.get('count', 3)))
        self.repeat_count_spinbox.blockSignals(False)

    def on_apply_button_clicked(self):
        if not self.current_selected_node: return
        node = self.current_selected_node
        old_data = {'name': node.node_name, 'desc': node.description, 'props': node.properties}
        new_props_list = []
        if isinstance(node, TriggerNode):
            trigger_type = self.trigger_type_combo.currentText()
            selected_zones = [self.zones_layout.itemAt(i).widget().property("zone_id") for i in
                              range(self.zones_layout.count()) if self.zones_layout.itemAt(i).widget().isChecked()]
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
        else:
            new_props_list = node.properties

        new_data = {'name': self.prop_name.text(), 'desc': self.prop_description.toPlainText(), 'props': new_props_list}
        if old_data != new_data:
            command = ChangePropertiesCommand(node, old_data, new_data)
            self.undo_stack.push(command)

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
        self.validate_scenario()
        if self.current_selected_node: self.on_selection_changed()

    def add_device(self):
        device_type = self.device_type_combo.currentText()
        spec = DEVICE_SPECS[device_type]
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
        table, name_prefix, extra_data = self.users_table, "Новий користувач", {'phone': ''}
        new_item = {'id': str(uuid.uuid4()), 'name': f"{name_prefix} {table.rowCount() + 1}", **extra_data}
        self.project_data['config'][config_key].append(new_item)
        self.update_config_ui()

    def remove_config_item(self, config_key):
        if config_key != 'users': return
        row = self.users_table.currentRow()
        if row > -1:
            del self.project_data['config'][config_key][row]
            self.update_config_ui()

    def on_config_table_changed(self, item):
        table = item.tableWidget()
        row, col = item.row(), item.column()
        item_id = table.item(row, 0).text()
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
        self.update_config_ui()

    def add_node(self, node_type, position):
        if self.active_scenario_id is None: return
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
        self.project_data = {'scenarios': {}, 'config': {'devices': [], 'users': [
            {'id': str(uuid.uuid4()), 'name': 'Адміністратор', 'phone': '+380000000000'}]}}
        self.device_type_combo.setCurrentText("ППКП Tiras-8L")
        self.add_device()
        self.active_scenario_id = None;
        self.current_selected_node = None
        self.update_scenarios_list();
        self.update_config_ui()
        self.props_widget.setEnabled(False)
        self.undo_stack.clear()
        self.add_scenario("Сценарій 1")

    def add_scenario(self, name=None):
        if name is None:
            i = 1;
            name = "Новий сценарій"
            while name in self.project_data['scenarios']: name = f"Новий сценарій {i}"; i += 1
        if name in self.project_data['scenarios']: return
        self.project_data['scenarios'][name] = {'nodes': [], 'connections': [], 'comments': []}
        self.update_scenarios_list()
        items = self.scenarios_list.findItems(name, Qt.MatchFlag.MatchExactly)
        if items: self.scenarios_list.setCurrentItem(items[0])

    def remove_scenario(self):
        if not self.active_scenario_id or self.scenarios_list.count() <= 1: return
        del self.project_data['scenarios'][self.active_scenario_id]
        self.active_scenario_id = None
        self.update_scenarios_list();
        self.scenarios_list.setCurrentRow(0)
        self.undo_stack.clear()

    def on_scenario_item_double_clicked(self, item):
        self._old_scenario_name = item.text()
        self.scenarios_list.editItem(item)

    def on_scenario_renamed(self, item):
        new_name = item.text().strip()
        old_name = self._old_scenario_name
        if not new_name or not old_name or new_name == old_name:
            if old_name: item.setText(old_name)
            return
        if new_name in self.project_data['scenarios']:
            QMessageBox.warning(self, "Помилка перейменування", "Сценарій з такою назвою вже існує.")
            item.setText(old_name)
            return
        self.project_data['scenarios'][new_name] = self.project_data['scenarios'].pop(old_name)
        if self.active_scenario_id == old_name: self.active_scenario_id = new_name
        self.update_scenarios_list()
        self._old_scenario_name = None

    def on_active_scenario_changed(self, current_item, previous_item):
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

    def save_current_scenario_state(self):
        if not self.active_scenario_id or self.active_scenario_id not in self.project_data['scenarios']: return
        nodes_data, connections_data, comments_data = [], [], []
        for item in self.scene.items():
            if hasattr(item, 'to_data'):
                data = item.to_data()
                if not data: continue
                if isinstance(item, BaseNode):
                    nodes_data.append(data)
                elif isinstance(item, Connection):
                    connections_data.append(data)
                elif isinstance(item, CommentItem):
                    comments_data.append(data)
        self.project_data['scenarios'][self.active_scenario_id] = {'nodes': nodes_data, 'connections': connections_data,
                                                                   'comments': comments_data}

    def load_scenario_state(self, scenario_id):
        self.scene.clear()
        scenario_data = self.project_data['scenarios'].get(scenario_id, {})
        nodes_map = {}
        for node_data in scenario_data.get('nodes', []):
            node = BaseNode.from_data(node_data)
            self.scene.addItem(node);
            nodes_map[node.id] = node
        for conn_data in scenario_data.get('connections', []):
            start_node = nodes_map.get(conn_data['from_node'])
            end_node = nodes_map.get(conn_data['to_node'])
            start_socket = start_node.get_socket(conn_data.get('from_socket', 'out'))
            end_socket = end_node.get_socket('in') if end_node else None

            if start_node and end_node and start_socket and end_socket:
                self.scene.addItem(Connection(start_socket, end_socket))
        for comment_data in scenario_data.get('comments', []):
            self.scene.addItem(CommentItem.from_data(comment_data, self.view))

        self._update_simulation_trigger_zones()

        self.validate_scenario()
        for item in self.scene.items():
            if isinstance(item, BaseNode): item.update_display_properties(self.project_data['config'])

    def update_scenarios_list(self):
        current_id = self.active_scenario_id
        self.scenarios_list.blockSignals(True)
        self.scenarios_list.clear()
        for name in sorted(self.project_data.get('scenarios', {}).keys()):
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            self.scenarios_list.addItem(item)
        if current_id:
            items = self.scenarios_list.findItems(current_id, Qt.MatchFlag.MatchExactly)
            if items: self.scenarios_list.setCurrentItem(items[0])
        self.scenarios_list.blockSignals(False)

    def validate_scenario(self):
        QTimer.singleShot(1, self._perform_validation)

    def _perform_validation(self):
        if not self.scene: return

        all_nodes = []
        trigger_node = None

        for item in self.scene.items():
            if isinstance(item, BaseNode):
                all_nodes.append(item)
                item.validate(self.project_data['config'])
                if isinstance(item, TriggerNode):
                    trigger_node = item

        if not trigger_node:
            for node in all_nodes:
                if not isinstance(node, TriggerNode):
                    node.set_validation_state(False, "В сценарії відсутній тригер.")
            return

        reachable_nodes = set()
        nodes_to_visit = [trigger_node]
        reachable_nodes.add(trigger_node)

        q = [trigger_node]
        visited = {trigger_node}
        while q:
            current_node = q.pop(0)
            for socket in current_node.get_output_sockets():
                for conn in socket.connections:
                    next_node = conn.end_socket.parentItem()
                    if isinstance(next_node, BaseNode) and next_node not in visited:
                        visited.add(next_node)
                        q.append(next_node)

        reachable_nodes = visited

        TERMINAL_NODE_TYPES = (ActivateOutputNode, DeactivateOutputNode, SendSMSNode)

        for node in all_nodes:
            if node not in reachable_nodes:
                if not node.error_icon.isVisible():
                    node.set_validation_state(False, "Узел недостижим от триггера.")
            elif not node.error_icon.isVisible():
                is_terminal = isinstance(node, TERMINAL_NODE_TYPES)

                # For non-condition nodes, check if they have any output connection
                if not isinstance(node, ConditionNodeZoneState) and not is_terminal:
                    if not any(sock.connections for sock in node.get_output_sockets()):
                        node.set_validation_state(False, "Цепочка логики не завершена действием.")

    def _update_simulation_trigger_zones(self):
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
        is_ready_for_sim = False
        if self.scene:
            trigger_node = next((item for item in self.scene.items() if isinstance(item, TriggerNode)), None)
            if trigger_node and trigger_node.error_icon.isVisible() is False:
                is_ready_for_sim = self.sim_trigger_zone_combo.count() > 0 and self.sim_trigger_zone_combo.currentData() is not None

        is_running = self.simulator.is_running
        self.start_sim_action.setEnabled(is_ready_for_sim and not is_running)
        self.step_sim_action.setEnabled(is_running)
        self.stop_sim_action.setEnabled(is_running)
        self.sim_trigger_zone_combo.setEnabled(not is_running)

    def start_simulation(self):
        self.validate_scenario()
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
        self.simulator.step()
        if not self.simulator.is_running:
            self.show_status_message("Симуляція завершена.", color="lime")
            self.stop_simulation()
        else:
            self.update_simulation_controls()

    def stop_simulation(self):
        self.simulator.stop()
        self.view.set_interactive(True)
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
        if color: QTimer.singleShot(timeout, lambda: self.statusBar().setStyleSheet(""))

    def copy_selection(self):
        selected_nodes = [item for item in self.scene.selectedItems() if isinstance(item, BaseNode)]
        if not selected_nodes: return
        clipboard_root = ET.Element("clipboard_data")
        nodes_xml = ET.SubElement(clipboard_root, "nodes")
        connections_xml = ET.SubElement(clipboard_root, "connections")
        selected_node_ids = {node.id for node in selected_nodes}
        for node in selected_nodes: node.to_xml(nodes_xml)
        for item in self.scene.items():
            if isinstance(item, Connection):
                start_node = item.start_socket.parentItem()
                end_node = item.end_socket.parentItem()
                if start_node and end_node and start_node.id in selected_node_ids and end_node.id in selected_node_ids:
                    item.to_xml(connections_xml)
        clipboard_string = ET.tostring(clipboard_root, pretty_print=True, encoding="unicode")
        QApplication.clipboard().setText(clipboard_string)
        self.show_status_message(f"Скопійовано {len(selected_nodes)} вузол(и).")

    def paste_at_center(self):
        self.paste_selection()

    def paste_selection(self, view_pos=None):
        clipboard_string = QApplication.clipboard().text()
        if not clipboard_string: return
        paste_pos = self.view.mapToScene(view_pos or self.view.viewport().rect().center())
        command = PasteCommand(self.scene, clipboard_string, paste_pos)
        self.undo_stack.push(command)

    def _load_project_data_from_file(self, path):
        log.debug(f"Attempting to load project from: {path}")
        try:
            root_xml = ET.parse(path).getroot()
            config_xml = root_xml.find("config")
            new_project_data = {'scenarios': {}, 'config': {'devices': [], 'users': []}}

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

            scenarios_xml = root_xml.find("scenarios")
            if scenarios_xml is not None:
                log.debug("Parsing <scenarios> section...")
                for scenario_el in scenarios_xml:
                    scenario_id = scenario_el.get("id")
                    log.debug(f"Parsing scenario ID: {scenario_id}")
                    if not scenario_id: continue
                    nodes_data, connections_data, comments_data = [], [], []

                    nodes_xml = scenario_el.find("nodes")
                    if nodes_xml is not None:
                        for node_el in nodes_xml:
                            log.debug(f"  - Parsing node ID: {node_el.get('id')}")
                            nodes_data.append(BaseNode.data_from_xml(node_el))

                    connections_xml = scenario_el.find("connections")
                    if connections_xml is not None:
                        for conn_el in connections_xml:
                            log.debug(
                                f"  - Parsing connection from: {conn_el.get('from_node')} to: {conn_el.get('to_node')}")
                            connections_data.append(Connection.data_from_xml(conn_el))

                    comments_xml = scenario_el.find("comments")
                    if comments_xml is not None:
                        for comment_el in comments_xml:
                            log.debug(f"  - Parsing comment ID: {comment_el.get('id')}")
                            comments_data.append(CommentItem.data_from_xml(comment_el))

                    new_project_data['scenarios'][scenario_id] = {'nodes': nodes_data, 'connections': connections_data,
                                                                  'comments': comments_data}
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
            self.active_scenario_id = None
            self.current_selected_node = None

            log.debug("Updating UI after import...")
            self.update_config_ui()
            self.update_scenarios_list()
            self.props_widget.setEnabled(False)

            scenario_keys = sorted(self.project_data.get('scenarios', {}).keys())
            if scenario_keys:
                first_scenario_id = scenario_keys[0]
                log.debug(f"Setting first scenario active: {first_scenario_id}")
                items = self.scenarios_list.findItems(first_scenario_id, Qt.MatchFlag.MatchExactly)
                if items: self.scenarios_list.setCurrentItem(items[0])

            self.scenarios_list.blockSignals(False)
            # Manually trigger the scenario change to load the data
            if self.scenarios_list.currentItem():
                self.on_active_scenario_changed(self.scenarios_list.currentItem(), None)

            self.show_status_message(f"Проект успішно імпортовано з {path}", color="green")
            log.info("Project imported successfully.")

        except Exception as e:
            log.critical(f"An unhandled exception occurred during project import: {e}", exc_info=True)
            QMessageBox.critical(self, "Помилка імпорту", f"Не вдалося імпортувати проект:\n{e}")
            self.new_project()

    def export_project(self):
        self.save_current_scenario_state()
        path, _ = QFileDialog.getSaveFileName(self, "Експорт проекту", "", "XML Files (*.xml)")
        if not path: return
        try:
            root_xml = ET.Element("project")
            config_xml = ET.SubElement(root_xml, "config")
            devices_xml = ET.SubElement(config_xml, "devices")
            for device in self.project_data['config'].get('devices', []):
                device_el = ET.SubElement(devices_xml, "device", id=device['id'], name=device['name'],
                                          type=device['type'])
                zones_xml = ET.SubElement(device_el, 'zones')
                outputs_xml = ET.SubElement(device_el, 'outputs')
                for zone in device.get('zones', []): ET.SubElement(zones_xml, 'zone', id=zone['id'], name=zone['name'])
                for output in device.get('outputs', []): ET.SubElement(outputs_xml, 'output', id=output['id'],
                                                                       name=output['name'])
            users_xml = ET.SubElement(config_xml, "users")
            for user in self.project_data['config'].get('users', []): ET.SubElement(users_xml, "user", id=user['id'],
                                                                                    name=user['name'],
                                                                                    phone=user.get('phone', ''))
            scenarios_xml = ET.SubElement(root_xml, "scenarios")
            for scenario_id, scenario_data in self.project_data['scenarios'].items():
                scenario_el = ET.SubElement(scenarios_xml, "scenario", id=scenario_id)
                nodes_xml = ET.SubElement(scenario_el, "nodes")
                connections_xml = ET.SubElement(scenario_el, "connections")
                comments_xml = ET.SubElement(scenario_el, "comments")
                for node_data in scenario_data.get('nodes', []): BaseNode.data_to_xml(nodes_xml, node_data)
                for conn_data in scenario_data.get('connections', []): Connection.data_to_xml(connections_xml,
                                                                                              conn_data)
                for comment_data in scenario_data.get('comments', []): CommentItem.data_to_xml(comments_xml,
                                                                                               comment_data)
            tree = ET.ElementTree(root_xml);
            tree.write(path, pretty_print=True, xml_declaration=True, encoding="utf-8")
            self.show_status_message(f"Проект успішно експортовано до {path}", color="green")
        except Exception as e:
            QMessageBox.critical(self, "Помилка експорту", f"Не вдалося експортувати проект:\n{e}")

    def add_comment(self):
        if self.active_scenario_id is None: return
        center_pos = self.view.mapToScene(self.view.viewport().rect().center())
        command = AddCommentCommand(self.scene, center_pos, self.view)
        self.undo_stack.push(command)

