import uuid
import logging  # Додано
from copy import deepcopy  # Додано для копіювання структур даних
from lxml import etree as ET
from PyQt6.QtGui import QUndoCommand
from PyQt6.QtCore import QPointF, QRectF
# Додано імпорти для запиту імені макросу
from PyQt6.QtWidgets import QInputDialog, QLineEdit, QMessageBox  # Додано QMessageBox

# Імпортуємо DecoratorNode для перевірки в AddConnectionCommand
# --- ЗМІНА: Додаємо імпорти для UngroupMacroCommand ---
from nodes import (BaseNode, Connection, CommentItem, FrameItem, TriggerNode, DecoratorNode, MacroNode,
                   MacroInputNode, MacroOutputNode, NODE_REGISTRY, generate_short_id)
# --- КІНЕЦЬ ЗМІНИ ---

# --- ИСПРАВЛЕНО: Импортируем константы из нового файла ---
from constants import EDIT_MODE_SCENARIO, EDIT_MODE_MACRO

# --- ЗМІНА: Імпортуємо утиліти для сцени (для UngroupMacroCommand) ---
from scene_utils import populate_scene_from_data, extract_data_from_scene

# --- КІНЕЦЬ ЗМІНИ ---


log = logging.getLogger(__name__)  # Додано


class AddNodeCommand(QUndoCommand):
    def __init__(self, scene, node_type_name, position, parent=None):
        super().__init__(parent)
        self.scene = scene
        self.node_type_name = node_type_name
        self.position = position
        node_class = NODE_REGISTRY.get(node_type_name, BaseNode)

        # Отримуємо main_window безпечно
        main_window = next((v.parent() for v in self.scene.views() if hasattr(v, 'parent') and callable(v.parent)),
                           None)
        self.main_window = main_window  # Зберігаємо для подальшого використання

        # --- [ИСПРАВЛЕНИЕ 1 и 2] Создаем уникальное имя для входов/выходов макроса ---
        node_name_to_pass = None
        # Перевіряємо режим редагування БЕЗПЕЧНО
        is_macro_mode = self.main_window and self.main_window.current_edit_mode == EDIT_MODE_MACRO

        if node_class in (MacroInputNode, MacroOutputNode) and is_macro_mode:
            base_name = "Вхід" if node_class is MacroInputNode else "Вихід"
            existing_names = set()
            for item in self.scene.items():
                if isinstance(item, node_class):
                    existing_names.add(item.node_name)
            i = 1
            new_name = f"{base_name} {i}"
            while new_name in existing_names:
                i += 1
                new_name = f"{base_name} {i}"
            node_name_to_pass = new_name
            log.debug(f"AddNodeCommand: Generated unique name '{new_name}' for {node_type_name}")
        # --- [КОНЕЦ ИСПРАВЛЕНИЯ] ---

        # --- [ИЗМЕНЕНО] Передаем имя в конструктор, если оно было создано ---
        if node_name_to_pass:
            self.node = node_class(name=node_name_to_pass)
        else:
            self.node = node_class()  # Стандартное поведение

        self.node.setPos(self.position)
        self.setText(f"Додати вузол {self.node.node_type}")

        if self.main_window and hasattr(self.main_window, 'project_manager'):  # Перевіряємо наявність менеджера
            config_data = self.main_window.project_manager.get_config_data()
            self.node.update_display_properties(config_data)
        elif self.main_window:
            log.warning("AddNodeCommand: Could not find ProjectManager on MainWindow.")
        else:
            log.warning("AddNodeCommand: Could not find MainWindow.")

    def redo(self):
        # --- Додано діагностичне логування ---
        log.debug(f"Redo AddNodeCommand: Adding node {self.node.id} ({self.node.node_type}) at {self.node.pos()}")
        try:
            self.scene.addItem(self.node)
            self.scene.clearSelection()
            self.node.setSelected(True)
            log.debug(f"  Node {self.node.id} added and selected.")
        except Exception as e:
            log.error(f"  Error during redo AddNodeCommand for node {self.node.id}: {e}", exc_info=True)

    def undo(self):
        # --- Додано діагностичне логування ---
        log.debug(f"Undo AddNodeCommand: Removing node {self.node.id} ({self.node.node_type})")
        if self.node.scene() == self.scene:
            try:
                self.scene.removeItem(self.node)
                log.debug(f"  Node {self.node.id} removed.")
            except Exception as e:
                log.error(f"  Error during undo AddNodeCommand for node {self.node.id}: {e}", exc_info=True)
        else:
            log.debug(f"  Node {self.node.id} not on scene, skipping removal.")


class AddNodeAndConnectCommand(QUndoCommand):
    def __init__(self, scene, node_type_name, position, start_node_id, start_socket_name, parent=None):
        super().__init__(parent)
        self.scene = scene
        self.node_type_name = node_type_name
        self.position = position
        self.start_node_id = start_node_id
        self.start_socket_name = start_socket_name

        node_class = NODE_REGISTRY.get(node_type_name, BaseNode)
        self.new_node = node_class()
        self.new_node.setPos(self.position)
        self.connection = None
        self.setText(f"Додати і з'єднати вузол {self.new_node.node_type}")
        self.main_window = next((v.parent() for v in self.scene.views() if hasattr(v, 'parent') and callable(v.parent)),
                                None)

    def redo(self):
        # --- Додано діагностичне логування ---
        log.debug(
            f"Redo AddNodeAndConnectCommand: Adding node {self.new_node.id} ({self.node_type_name}) and connecting from {self.start_node_id}:{self.start_socket_name}")
        try:
            # Перевіряємо, чи вузол вже на сцені (після undo)
            if self.new_node.scene() != self.scene:
                self.scene.addItem(self.new_node)
                log.debug(f"  Node {self.new_node.id} added to scene.")
            else:
                log.debug(f"  Node {self.new_node.id} already on scene.")

            start_node = next(
                (item for item in self.scene.items() if isinstance(item, BaseNode) and item.id == self.start_node_id),
                None)
            if not start_node:
                log.error(f"  Start node {self.start_node_id} not found.")
                self.setObsolete(True)
                if self.new_node.scene() == self.scene: self.scene.removeItem(self.new_node)
                return

            start_socket = start_node.get_socket(self.start_socket_name)
            end_socket = self.new_node.get_socket("in")

            if not (start_socket and end_socket):
                log.error(f"  Socket not found (start={start_socket}, end={end_socket})")
                self.setObsolete(True)
                if self.new_node.scene() == self.scene: self.scene.removeItem(self.new_node)
                return

            is_output_from_start = start_socket.is_output

            if self.connection is None:
                log.debug("  Creating new connection object.")
                if is_output_from_start and not end_socket.is_output:
                    self.connection = Connection(start_socket, end_socket)
                elif not is_output_from_start and end_socket.is_output:
                    self.connection = Connection(end_socket, start_socket)
                else:
                    log.error(f"  Invalid connection direction.")
                    self.setObsolete(True)
                    if self.new_node.scene() == self.scene: self.scene.removeItem(self.new_node)
                    return
            else:  # Відновлення з'єднання після undo
                log.debug("  Restoring existing connection object.")
                start_socket_restored = start_node.get_socket(self.start_socket_name)
                end_socket_restored = self.new_node.get_socket("in")
                if start_socket_restored and end_socket_restored:
                    self.connection.start_socket = start_socket_restored
                    self.connection.end_socket = end_socket_restored
                    # Додаємо з'єднання до сокетів, якщо їх там немає
                    if self.connection not in self.connection.start_socket.connections:
                        self.connection.start_socket.add_connection(self.connection)
                    if self.connection not in self.connection.end_socket.connections:
                        self.connection.end_socket.add_connection(self.connection)
                    log.debug(f"    Restored connection sockets.")
                else:
                    log.error("    Failed to restore sockets for existing connection.")
                    self.setObsolete(True)
                    if self.new_node.scene() == self.scene: self.scene.removeItem(self.new_node)
                    if self.connection.scene() == self.scene: self.scene.removeItem(self.connection)
                    return

            # Додаємо з'єднання на сцену, якщо його там немає
            if self.connection.scene() != self.scene:
                self.scene.addItem(self.connection)
                log.debug(f"  Connection added to scene.")

            self.connection.update_path()
            log.debug(f"  Connection path updated.")

            if self.main_window and hasattr(self.main_window, 'project_manager'):
                config_data = self.main_window.project_manager.get_config_data()
                self.new_node.update_display_properties(config_data)
                log.debug(f"  Node display properties updated.")
            else:
                log.warning(f"  Could not update display properties: MainWindow or ProjectManager not found.")

            self.scene.clearSelection()
            self.new_node.setSelected(True)
            log.debug(f"Redo AddNodeAndConnectCommand finished successfully for node {self.new_node.id}.")

        except Exception as e:
            log.error(f"Error during redo AddNodeAndConnectCommand: {e}", exc_info=True)
            # Спроба очищення
            if self.connection and self.connection.scene() == self.scene:
                try:
                    self.scene.removeItem(self.connection)
                except Exception:
                    pass
            if self.new_node and self.new_node.scene() == self.scene:
                try:
                    self.scene.removeItem(self.new_node)
                except Exception:
                    pass
            self.setObsolete(True)

    def undo(self):
        # --- Додано діагностичне логування ---
        log.debug(f"Undo AddNodeAndConnectCommand: Removing node {self.new_node.id} and connection.")
        try:
            if self.connection:
                start_socket = self.connection.start_socket
                end_socket = self.connection.end_socket
                # Обережно видаляємо посилання
                if start_socket and self.connection in start_socket.connections:
                    start_socket.remove_connection(self.connection)
                    log.debug(f"  Removed connection from start socket {start_socket.socket_name}.")
                if end_socket and self.connection in end_socket.connections:
                    end_socket.remove_connection(self.connection)
                    log.debug(f"  Removed connection from end socket {end_socket.socket_name}.")
                # Видаляємо зі сцени
                if self.connection.scene() == self.scene:
                    self.scene.removeItem(self.connection)
                    log.debug("  Connection removed from scene.")

            if self.new_node.scene() == self.scene:
                self.scene.removeItem(self.new_node)
                log.debug(f"  Node {self.new_node.id} removed from scene.")
            log.debug("Undo AddNodeAndConnectCommand finished.")
        except Exception as e:
            log.error(f"Error during undo AddNodeAndConnectCommand: {e}", exc_info=True)


# ... (решта команд) ...


# --- Команда для створення макросу (оновлена) ---
class CreateMacroCommand(QUndoCommand):
    def __init__(self, main_window, selected_items, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        # --- ВИПРАВЛЕННЯ: Отримуємо project_manager ---
        if hasattr(main_window, 'project_manager'):
            self.project_manager = main_window.project_manager
        else:
            log.error("CreateMacroCommand init failed: MainWindow has no project_manager.")
            self.project_manager = None  # Або генерувати виключення
        # --- КІНЕЦЬ ВИПРАВЛЕННЯ ---
        self.scene = main_window.scene
        self.removed_items_data = []
        self.external_connections_data = []
        self.macro_id = None
        self.macro_node_id = None
        self.new_external_connections_refs = []
        self.initial_selected_ids = {item.id for item in selected_items if hasattr(item, 'id')}
        self.setText("Створити Макрос")
        log.debug(f"CreateMacroCommand initialized with {len(self.initial_selected_ids)} selected item IDs.")

    def redo(self):
        log.debug("CreateMacroCommand redo executing...")
        # --- ВИПРАВЛЕННЯ: Перевіряємо наявність project_manager ---
        if not self.project_manager:
            log.error("CreateMacroCommand redo aborted: project_manager is not available.")
            self.setObsolete(True)
            return
        # --- КІНЕЦЬ ВИПРАВЛЕННЯ ---

        macro_node = None

        if self.macro_id:
            log.debug(f"Redoing macro creation for {self.macro_id}...")
            # --- ВИПРАВЛЕННЯ: Використовуємо project_manager ---
            if hasattr(self, 'macro_data_backup') and not self.project_manager.get_macro_data(self.macro_id):
                # Відновлюємо дані макросу в менеджері
                self.project_manager.add_or_update_macro(self.macro_id, self.macro_data_backup)
                log.debug(f"Restored macro definition {self.macro_id} in ProjectManager.")
            # --- КІНЕЦЬ ВИПРАВЛЕННЯ ---
            elif not hasattr(self, 'macro_data_backup'):
                log.warning("Cannot redo macro definition restoration - backup data missing.")

            self._remove_restored_items()
            macro_node = self._find_macro_node()
            if not macro_node:
                # --- ВИПРАВЛЕННЯ: Використовуємо project_manager ---
                macro_data = self.project_manager.get_macro_data(self.macro_id)
                # --- КІНЕЦЬ ВИПРАВЛЕННЯ ---
                if macro_data:
                    macro_node = self._create_and_add_macro_node(macro_data)
                else:
                    log.error(f"Cannot recreate MacroNode: definition {self.macro_id} not found.")
                    self.setObsolete(True);
                    return
            if macro_node:
                self.new_external_connections_refs = self._reconnect_external_connections(macro_node,
                                                                                          self.external_connections_data)

            self.main_window.update_macros_list()  # Залишається, оновлює UI
            log.debug("Macro creation redo finished.")
            return

        log.debug("First execution: Creating macro...")
        selected_items = {item for item in self.scene.items() if
                          hasattr(item, 'id') and item.id in self.initial_selected_ids}
        if not selected_items:
            log.warning("CreateMacroCommand redo: Initial selected items not found.");
            self.setObsolete(True);
            return

        try:
            macro_data, external_connections_info = self._create_macro_definition_and_analyze(selected_items)
            if not macro_data: self.setObsolete(True); return
            self.macro_id = macro_data['id']
            self.macro_data_backup = deepcopy(macro_data)  # Зберігаємо копію для undo/redo
            self.external_connections_data = external_connections_info
            # --- ВИПРАВЛЕННЯ: Додаємо визначення макросу в менеджер ---
            self.project_manager.add_or_update_macro(self.macro_id, macro_data)
            log.debug(f"Added macro definition {self.macro_id} to ProjectManager.")
            # --- КІНЕЦЬ ВИПРАВЛЕННЯ ---
        except Exception as e:
            log.error(f"Error during macro definition: {e}", exc_info=True); self.setObsolete(True); return

        self.removed_items_data = []
        items_to_remove = set(selected_items)  # Починаємо з вибраних
        internal_connections = self._find_internal_connections(selected_items)
        items_to_remove.update(internal_connections)  # Додаємо внутрішні з'єднання
        items_to_remove.update(
            {info['original_conn'] for info in external_connections_info})  # Додаємо зовнішні з'єднання

        for item in items_to_remove:
            item_type = None
            if isinstance(item, BaseNode):
                item_type = 'node'
            elif isinstance(item, Connection):
                item_type = 'connection'
            elif isinstance(item, CommentItem):
                item_type = 'comment'
            elif isinstance(item, FrameItem):
                item_type = 'frame'
            if item_type:
                try:
                    item_data = item.to_data()
                    if item_data: self.removed_items_data.append({'type': item_type, 'data': item_data})
                except Exception as e:
                    log.error(f"Error getting data for removed item {item}: {e}", exc_info=True)

        self._remove_items_by_objects(items_to_remove)
        macro_node = self._create_and_add_macro_node(macro_data)
        if not macro_node: return

        self.new_external_connections_refs = self._reconnect_external_connections(macro_node, external_connections_info)
        self.main_window.update_macros_list()  # Оновлюємо UI
        log.debug(f"Macro {self.macro_id} created successfully.")

    def undo(self):
        log.debug(f"CreateMacroCommand undo executing for macro {self.macro_id}...")
        # --- ВИПРАВЛЕННЯ: Перевіряємо наявність project_manager ---
        if not self.project_manager:
            log.error("CreateMacroCommand undo aborted: project_manager is not available.")
            # Не робимо obsolete, бо це може бути тимчасово
            return
        # --- КІНЕЦЬ ВИПРАВЛЕННЯ ---

        macro_node = self._find_macro_node()
        if macro_node and macro_node.scene() == self.scene:
            for conn_ref in self.new_external_connections_refs:
                conn = self._find_connection_by_refs(conn_ref['start_ref'], conn_ref['end_ref'])
                if conn and conn.scene() == self.scene:
                    if conn.start_socket: conn.start_socket.remove_connection(conn)
                    if conn.end_socket: conn.end_socket.remove_connection(conn)
                    self.scene.removeItem(conn)
            self.new_external_connections_refs = []
            self.scene.removeItem(macro_node)
            log.debug(f"Removed MacroNode {self.macro_node_id}")

        self._restore_removed_items()

        # --- ВИПРАВЛЕННЯ: Використовуємо project_manager ---
        if self.macro_id and self.project_manager.get_macro_data(self.macro_id):
            # Зберігаємо копію перед видаленням, якщо її ще немає
            if not hasattr(self, 'macro_data_backup'):
                self.macro_data_backup = deepcopy(self.project_manager.get_macro_data(self.macro_id))
            # Видаляємо визначення з менеджера
            if self.project_manager.remove_macro(self.macro_id):
                log.debug(f"Removed macro definition {self.macro_id} from ProjectManager.")
            else:
                log.warning(f"Could not remove macro definition {self.macro_id} from ProjectManager.")
        # --- КІНЕЦЬ ВИПРАВЛЕННЯ ---

        self.main_window.update_macros_list()  # Оновлюємо UI
        log.debug("CreateMacroCommand undo finished.")

    # --- Допоміжні методи ---

    def _find_macro_node(self):
        if not self.macro_node_id or not self.scene: return None
        return next(
            (item for item in self.scene.items() if isinstance(item, MacroNode) and item.id == self.macro_node_id),
            None)

    def _find_connection_by_refs(self, start_ref, end_ref):
        start_node = next(
            (item for item in self.scene.items() if isinstance(item, BaseNode) and item.id == start_ref['node_id']),
            None)
        end_node = next(
            (item for item in self.scene.items() if isinstance(item, BaseNode) and item.id == end_ref['node_id']), None)
        if start_node and end_node:
            start_socket = start_node.get_socket(start_ref['socket_name'])
            end_socket = end_node.get_socket(end_ref['socket_name'])
            if start_socket:
                for conn in start_socket.connections:
                    if conn.end_socket == end_socket: return conn
        return None

    def _get_items_by_ids(self, ids):
        if not self.scene: return set()
        id_set = set(ids)
        return {item for item in self.scene.items() if hasattr(item, 'id') and item.id in id_set}

    def _find_internal_connections(self, item_set):
        internal_connections = set()
        node_ids = {item.id for item in item_set if isinstance(item, BaseNode)}
        all_connections = [item for item in self.scene.items() if isinstance(item, Connection)]
        for conn in all_connections:
            start_node = conn.start_socket.parentItem() if conn.start_socket else None
            end_node = conn.end_socket.parentItem() if conn.end_socket else None
            if start_node and end_node and start_node.id in node_ids and end_node.id in node_ids:
                internal_connections.add(conn)
        return internal_connections

    def _remove_items_by_objects(self, items_to_remove):
        log.debug(f"Removing {len(items_to_remove)} items...")
        connections_first = {item for item in items_to_remove if isinstance(item, Connection)}
        others = items_to_remove - connections_first
        for conn in connections_first:
            if conn.scene() == self.scene:
                try:
                    if conn.start_socket: conn.start_socket.remove_connection(conn)
                    if conn.end_socket: conn.end_socket.remove_connection(conn)
                    self.scene.removeItem(conn)
                except Exception as e:
                    log.error(f"Error removing conn: {e}", exc_info=True)
        for item in others:
            if item.scene() == self.scene:
                try:
                    self.scene.removeItem(item)
                except Exception as e:
                    log.error(f"Error removing item {getattr(item, 'id', item)}: {e}", exc_info=True)

    def _remove_restored_items(self):
        log.debug(f"Removing {len(self.removed_items_data)} restored items...")
        items_to_remove_now = set()
        restored_ids = {item_data['data'].get('id') for item_data in self.removed_items_data if
                        item_data['data'].get('id')}
        for item in self.scene.items():
            if hasattr(item, 'id') and item.id in restored_ids: items_to_remove_now.add(item)
        self._remove_items_by_objects(items_to_remove_now)

    def _restore_removed_items(self):
        log.debug(f"Restoring {len(self.removed_items_data)} items...")
        restored_nodes = {}
        view = self.scene.views()[0] if self.scene.views() else None

        for item_info in self.removed_items_data:
            item_type, item_data = item_info['type'], item_info['data']
            item_id = item_data.get('id')
            restored_item, existing_item = None, None
            if item_id: existing_item = next((i for i in self.scene.items() if hasattr(i, 'id') and i.id == item_id),
                                             None)
            if existing_item:
                log.debug(f"Item {item_id} exists. Using existing."); restored_item = existing_item;
            else:
                try:
                    log.debug(f"Attempt restore {item_type} ID {item_id}")
                    if item_type == 'node':
                        restored_item = BaseNode.from_data(item_data)
                    elif item_type == 'comment' and view:
                        restored_item = CommentItem.from_data(item_data, view)
                    elif item_type == 'frame' and view:
                        restored_item = FrameItem.from_data(item_data, view)
                    if restored_item:
                        log.debug(f"Adding restored {item_type} {item_id}"); self.scene.addItem(restored_item)
                    else:
                        log.warning(f"Failed create {item_type} ID {item_id}")
                except Exception as e:
                    log.error(f" Err restore {item_type} data {item_data}: {e}", exc_info=True)
            if item_type == 'node' and restored_item: restored_nodes[item_id] = restored_item

        for item_info in self.removed_items_data:
            if item_info['type'] == 'connection':
                conn_data = item_info['data']
                from_node_id, to_node_id = conn_data.get('from_node'), conn_data.get('to_node')
                log.debug(f"Attempt restore conn {from_node_id} -> {to_node_id}")
                if not from_node_id or not to_node_id: continue
                from_node = restored_nodes.get(from_node_id) or next(
                    (i for i in self.scene.items() if isinstance(i, BaseNode) and i.id == from_node_id), None)
                to_node = restored_nodes.get(to_node_id) or next(
                    (i for i in self.scene.items() if isinstance(i, BaseNode) and i.id == to_node_id), None)
                if from_node and to_node:
                    start_socket = from_node.get_socket(conn_data['from_socket'])
                    end_socket = to_node.get_socket(conn_data.get('to_socket', 'in'))
                    if start_socket and end_socket:
                        already_exists = any(conn.end_socket == end_socket for conn in start_socket.connections)
                        if not already_exists:
                            try:
                                conn = Connection(start_socket, end_socket); self.scene.addItem(
                                    conn); conn.update_path(); log.debug(
                                    f" OK restore conn {from_node_id} -> {to_node_id}")
                            except Exception as e:
                                log.error(f"Err create/add conn undo: {e}", exc_info=True)
                        else:
                            log.debug(f"Conn {from_node_id} -> {to_node_id} exists.")
                    else:
                        log.warning(f" Sockets not found for restore conn: {conn_data}")
                else:
                    log.warning(f" Nodes not found for restore conn: {conn_data}")

    def _create_macro_definition_and_analyze(self, selected_items):
        """Створює визначення макросу та аналізує зовнішні зв'язки."""
        # --- ВИПРАВЛЕННЯ: Перевіряємо наявність project_manager ---
        if not self.project_manager:
            log.error("_create_macro_definition_and_analyze aborted: project_manager is not available.")
            QMessageBox.critical(self.main_window, "Помилка", "Внутрішня помилка: Менеджер проекту недоступний.")
            return None, None
        # --- КІНЕЦЬ ВИПРАВЛЕННЯ ---

        # --- ВИПРАВЛЕННЯ: Використовуємо project_manager для отримання даних ---
        macros_count = len(self.project_manager.get_macros_data())
        # --- КІНЕЦЬ ВИПРАВЛЕННЯ ---
        macro_name_input, ok = QInputDialog.getText(
            self.main_window, "Створення Макросу",
            "Введіть ім'я для нового макросу:", QLineEdit.EchoMode.Normal,
            f"Макрос {macros_count + 1}"  # Використовуємо отриману кількість
        )
        if not ok or not macro_name_input.strip(): log.warning("Macro creation cancelled."); return None, None
        macro_name = macro_name_input.strip()

        # --- ВИПРАВЛЕННЯ: Використовуємо project_manager для перевірки імені ---
        if self.project_manager.is_macro_name_taken(macro_name):
            log.error(f"Macro name '{macro_name}' already exists.")
            QMessageBox.warning(self.main_window, "Помилка", f"Макрос з ім'ям '{macro_name}' вже існує.")
            return None, None
        # --- КІНЕЦЬ ВИПРАВЛЕННЯ ---

        macro_id = generate_short_id()
        log.debug(f"Generating macro definition. ID: {macro_id}, Name: {macro_name}")

        selected_nodes = {item for item in selected_items if isinstance(item, BaseNode)}
        selected_node_ids = {node.id for node in selected_nodes}
        selected_comments = {item for item in selected_items if isinstance(item, CommentItem)}
        selected_frames = {item for item in selected_items if isinstance(item, FrameItem)}

        internal_nodes_data, internal_connections_data = [], []
        internal_comments_data = [c.to_data() for c in selected_comments]
        internal_frames_data = [f.to_data() for f in selected_frames]
        potential_inputs, potential_outputs = [], []
        old_id_to_new_id = {}
        all_scene_connections = [item for item in self.scene.items() if isinstance(item, Connection)]
        min_x, min_y = float('inf'), float('inf');
        bounding_rect = QRectF()

        try:
            items_to_normalize = list(selected_nodes) + list(selected_comments) + list(selected_frames)
            if not items_to_normalize: return None, None
            for item in items_to_normalize:
                item_rect = item.sceneBoundingRect();
                item_pos = item.pos()
                bounding_rect = bounding_rect.united(item_rect) if bounding_rect.isValid() else item_rect
                min_x, min_y = min(min_x, item_pos.x()), min(min_y, item_pos.y())
                new_id, old_id = generate_short_id(), item.id
                old_id_to_new_id[old_id] = new_id
                item_data = item.to_data();
                item_data['id'] = new_id
                if isinstance(item, BaseNode):
                    internal_nodes_data.append(item_data)
                elif isinstance(item, CommentItem):
                    internal_comments_data.append(item_data)
                elif isinstance(item, FrameItem):
                    internal_frames_data.append(item_data)
            if min_x == float('inf'): log.error("Cannot determine bounds."); return None, None
            for data_list in [internal_nodes_data, internal_comments_data, internal_frames_data]:
                for item_data in data_list:
                    ox, oy = item_data['pos'];
                    item_data['pos'] = (ox - min_x, oy - min_y)
        except Exception as e:
            log.error(f"Error processing items: {e}", exc_info=True); return None, None

        normalized_bounding_rect = bounding_rect.translated(-min_x, -min_y)
        center_x, center_y = normalized_bounding_rect.center().x(), normalized_bounding_rect.center().y()
        normalized_width = normalized_bounding_rect.width()
        log.debug(f"Normalized center: ({center_x:.1f}, {center_y:.1f}), Width: {normalized_width:.1f}")

        external_connections_info = []
        try:
            for conn in all_scene_connections:
                start_node = conn.start_socket.parentItem() if conn.start_socket else None
                end_node = conn.end_socket.parentItem() if conn.end_socket else None
                if not start_node or not end_node: continue
                start_in, end_in = start_node.id in selected_node_ids, end_node.id in selected_node_ids
                if start_in and end_in:
                    conn_data = conn.to_data()
                    new_from, new_to = old_id_to_new_id.get(conn_data['from_node']), old_id_to_new_id.get(
                        conn_data['to_node'])
                    if new_from and new_to: conn_data['from_node'], conn_data[
                        'to_node'] = new_from, new_to; internal_connections_data.append(conn_data)
                elif not start_in and end_in:
                    potential_inputs.append((end_node.pos().y(), conn.to_data(), conn))
                # --- ЗМІНА: Додаємо X-координату ВНУТРІШНЬОГО вузла для сортування виходів ---
                elif start_in and not end_in:
                    potential_outputs.append((start_node.pos().x(), conn.to_data(), conn))
                # --- КІНЕЦЬ ЗМІНИ ---
        except Exception as e:
            log.error(f"Error analyzing connections: {e}", exc_info=True); return None, None

        try:
            # Сортуємо входи за Y-позицією зовнішнього вузла (залишаємо як було)
            potential_inputs.sort(key=lambda x: x[2].start_socket.parentItem().pos().y())
            log.debug("Sorted potential inputs by external node Y-pos.")  # Діагностика

            # --- ЗМІНА: Сортуємо виходи за X-позицією ВНУТРІШНЬОГО вузла ---
            # Використовуємо x[0], де ми зберегли start_node.pos().x()
            potential_outputs.sort(key=lambda x: x[0])
            log.debug("Sorted potential outputs by INTERNAL node X-pos.")  # Оновлено діагностику
            # --- КІНЕЦЬ ЗМІНИ ---
            # --- Додано діагностику відсортованих виходів ---
            log.debug(f"Sorted potential_outputs (by internal X):")  # Діагностика
            for x_pos, conn_data, conn_obj in potential_outputs:
                start_node = conn_obj.start_socket.parentItem()
                end_node = conn_obj.end_socket.parentItem()
                log.debug(
                    f"  - Internal Node: {start_node.id} ({start_node.node_name}) at X={x_pos:.1f} -> External Node: {end_node.id} ({end_node.node_name})")  # Діагностика
            # --- КІНЕЦЬ діагностики ---
        except Exception as e:
            log.error(f"Err sort IO: {e}. Fallback.", exc_info=True); potential_inputs.sort(
                key=lambda x: x[0]); potential_outputs.sort(key=lambda x: x[0])  # Fallback залишається

        macro_inputs, macro_outputs = [], []
        input_name_base, output_name_base = "Вхід", "Вихід"
        input_idx, output_idx = 1, 1

        for i, (_, original_conn_data, original_conn_obj) in enumerate(potential_inputs):
            input_name = f"{input_name_base} {input_idx}";
            input_idx += 1
            macro_input_node = MacroInputNode(name=input_name)
            input_x = -macro_input_node.width - 50
            input_y = center_y + (i - (len(potential_inputs) - 1) / 2) * 70
            input_node_data = macro_input_node.to_data();
            input_node_data['pos'] = (input_x, input_y)
            internal_nodes_data.append(input_node_data)
            log.debug(f"  Created MacroInput '{input_name}' at ({input_x:.1f}, {input_y:.1f})")
            internal_target_id = old_id_to_new_id.get(original_conn_data['to_node'])
            internal_target_sock = original_conn_data['to_socket']
            macro_inputs.append({'name': input_name, 'internal_node_id': internal_target_id,
                                 'internal_socket_name': internal_target_sock,
                                 'macro_input_node_id': input_node_data['id']})
            internal_connections_data.append(
                {'from_node': input_node_data['id'], 'from_socket': 'out', 'to_node': internal_target_id,
                 'to_socket': internal_target_sock})
            external_connections_info.append(
                {'type': 'input', 'original_conn_data': original_conn_data, 'target_input_name': input_name,
                 'original_conn': original_conn_obj})

        for i, (_, original_conn_data, original_conn_obj) in enumerate(potential_outputs):
            output_name = f"{output_name_base} {output_idx}";
            output_idx += 1
            macro_output_node = MacroOutputNode(name=output_name)
            output_x = normalized_width + 50
            output_y = center_y + (i - (len(potential_outputs) - 1) / 2) * 70
            output_node_data = macro_output_node.to_data();
            output_node_data['pos'] = (output_x, output_y)
            internal_nodes_data.append(output_node_data)
            log.debug(f"  Created MacroOutput '{output_name}' at ({output_x:.1f}, {output_y:.1f})")
            internal_source_id = old_id_to_new_id.get(original_conn_data['from_node'])
            internal_source_sock = original_conn_data['from_socket']
            macro_outputs.append({'name': output_name, 'internal_node_id': internal_source_id,
                                  'internal_socket_name': internal_source_sock,
                                  'macro_output_node_id': output_node_data['id']})
            internal_connections_data.append({'from_node': internal_source_id, 'from_socket': internal_source_sock,
                                              'to_node': output_node_data['id'], 'to_socket': 'in'})
            external_connections_info.append(
                {'type': 'output', 'original_conn_data': original_conn_data, 'source_output_name': output_name,
                 'original_conn': original_conn_obj})

        macro_data = {'id': macro_id, 'name': macro_name, 'nodes': internal_nodes_data,
                      'connections': internal_connections_data, 'comments': internal_comments_data,
                      'frames': internal_frames_data, 'inputs': macro_inputs, 'outputs': macro_outputs}
        # Визначення додається в redo() через project_manager
        log.debug(f"Macro definition '{macro_id}' created (data prepared).")
        return macro_data, external_connections_info

    def _create_and_add_macro_node(self, macro_data=None):
        if not self.macro_id or not self.project_manager: log.error(
            "Cannot create MacroNode: Macro ID/PM missing."); self.setObsolete(True); return None
        if not macro_data: macro_data = self.project_manager.get_macro_data(self.macro_id)
        if not macro_data: log.error(f"Macro def {self.macro_id} not found."); self.setObsolete(True); return None
        center_pos = QPointF(0, 0)
        node_positions = [QPointF(*item['data']['pos']) for item in self.removed_items_data if
                          item['type'] in ['node', 'comment', 'frame']]
        if node_positions:
            try:
                cx = sum(p.x() for p in node_positions) / len(node_positions); cy = sum(
                    p.y() for p in node_positions) / len(node_positions); center_pos = QPointF(cx, cy)
            except ZeroDivisionError:
                log.warning("Cannot calc center pos."); center_pos = QPointF(
                    *self.removed_items_data[0]['data'].get('pos', (0, 0))) if self.removed_items_data else center_pos
        log.debug(f"Center pos for MacroNode: ({center_pos.x():.1f}, {center_pos.y():.1f})")
        try:
            macro_node = MacroNode(macro_id=self.macro_id, name=macro_data['name'])
            self.macro_node_id = macro_node.id;
            macro_node.setPos(center_pos)
            macro_node.update_sockets_from_definition(macro_data)
            self.scene.addItem(macro_node);
            self.scene.clearSelection();
            macro_node.setSelected(True)
            log.debug(f"Created/added MacroNode {self.macro_node_id} at {center_pos}")
            return macro_node
        except Exception as e:
            log.error(f"Error create/add MacroNode: {e}", exc_info=True); self.setObsolete(True); return None

    def _reconnect_external_connections(self, macro_node, external_connections_info=None):
        if external_connections_info is None: external_connections_info = self.external_connections_data
        log.debug(f"Reconnecting {len(external_connections_info)} external conns to {macro_node.id}...")
        new_connections_refs = []
        for info in external_connections_info:
            original_data = info['original_conn_data'];
            new_conn = None
            try:
                if info['type'] == 'input':
                    ext_node_id, ext_sock_name = original_data['from_node'], original_data['from_socket']
                    macro_sock_name = info['target_input_name']
                    ext_node = next((i for i in self.scene.items() if isinstance(i, BaseNode) and i.id == ext_node_id),
                                    None)
                    if ext_node:
                        ext_sock, macro_sock = ext_node.get_socket(ext_sock_name), macro_node.get_socket(
                            macro_sock_name)
                        if ext_sock and macro_sock and not any(
                                c.end_socket == macro_sock for c in ext_sock.connections):
                            new_conn = Connection(ext_sock, macro_sock);
                            log.debug(f"  Input: {ext_node_id}:{ext_sock_name} -> Macro:{macro_sock_name}")
                        elif not (ext_sock and macro_sock):
                            log.warning(
                                f"  Input fail: Sockets not found (ext:{ext_sock}, macro:{macro_sock}, name:{macro_sock_name})")
                    else:
                        log.warning(f"  Input fail: Ext node {ext_node_id} not found.")
                elif info['type'] == 'output':
                    ext_node_id, ext_sock_name = original_data['to_node'], original_data.get('to_socket', 'in')
                    macro_sock_name = info['source_output_name']
                    ext_node = next((i for i in self.scene.items() if isinstance(i, BaseNode) and i.id == ext_node_id),
                                    None)
                    if ext_node:
                        ext_sock, macro_sock = ext_node.get_socket(ext_sock_name), macro_node.get_socket(
                            macro_sock_name)
                        if ext_sock and macro_sock and not any(
                                c.end_socket == ext_sock for c in macro_sock.connections):
                            new_conn = Connection(macro_sock, ext_sock);
                            log.debug(f"  Output: Macro:{macro_sock_name} -> {ext_node_id}:{ext_sock_name}")
                        elif not (ext_sock and macro_sock):
                            log.warning(
                                f"  Output fail: Sockets not found (macro:{macro_sock}, ext:{ext_sock}, name:{macro_sock_name})")
                    else:
                        log.warning(f"  Output fail: Ext node {ext_node_id} not found.")
                if new_conn:
                    self.scene.addItem(new_conn)
                    new_connections_refs.append({'start_ref': {'node_id': new_conn.start_socket.parentItem().id,
                                                               'socket_name': new_conn.start_socket.socket_name},
                                                 'end_ref': {'node_id': new_conn.end_socket.parentItem().id,
                                                             'socket_name': new_conn.end_socket.socket_name}})
            except Exception as e:
                log.error(f" Err reconnecting {info}: {e}", exc_info=True)
        return new_connections_refs


# --- ДОДАНО: Команда розгрупування макросу ---
class UngroupMacroCommand(QUndoCommand):
    def __init__(self, main_window, macro_node, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.project_manager = main_window.project_manager
        self.scene = main_window.scene
        self.macro_node_data = macro_node.to_data()  # Зберігаємо дані вузла макросу
        self.macro_node_id = macro_node.id  # ID вузла на сцені
        self.macro_definition_id = macro_node.macro_id  # ID визначення макросу
        self.macro_node_pos = macro_node.pos()  # Позиція вузла макросу

        # Зберігаємо дані про зовнішні зв'язки вузла макросу
        self.incoming_connections_data = []  # Список dict {'node_id': ..., 'socket_name': ...}
        self.outgoing_connections_data = []  # Список dict {'node_id': ..., 'socket_name': ...}

        log.debug(
            f"UngroupMacroCommand init for MacroNode: {self.macro_node_id}, Definition: {self.macro_definition_id}")

        for socket_name, socket in macro_node._sockets.items():
            if not socket.is_output:  # Вхідні сокети вузла макросу (зовнішні підключення ДО макросу)
                for conn in socket.connections:
                    if conn.start_socket:  # Перевіряємо, чи існує початковий сокет
                        start_node = conn.start_socket.parentItem()
                        if start_node:
                            self.incoming_connections_data.append({
                                'external_node_id': start_node.id,
                                'external_socket_name': conn.start_socket.socket_name,
                                'macro_socket_name': socket_name  # Ім'я вхідного сокета макросу, до якого підключено
                            })
                            log.debug(
                                f"  Storing incoming connection: {start_node.id}:{conn.start_socket.socket_name} -> Macro:{socket_name}")
                        else:
                            log.warning(
                                f"  Could not store incoming connection to {socket_name}: start_node not found.")
            else:  # Вихідні сокети вузла макросу (зовнішні підключення ВІД макросу)
                for conn in socket.connections:
                    if conn.end_socket:  # Перевіряємо, чи існує кінцевий сокет
                        end_node = conn.end_socket.parentItem()
                        if end_node:
                            self.outgoing_connections_data.append({
                                'macro_socket_name': socket_name,  # Ім'я вихідного сокета макросу, з якого йде зв'язок
                                'external_node_id': end_node.id,
                                'external_socket_name': conn.end_socket.socket_name
                            })
                            log.debug(
                                f"  Storing outgoing connection: Macro:{socket_name} -> {end_node.id}:{conn.end_socket.socket_name}")
                        else:
                            log.warning(
                                f"  Could not store outgoing connection from {socket_name}: end_node not found.")

        # Дані для відновлення (заповнюються в redo)
        self.ungrouped_items_data = []  # Список {'type': ..., 'data': ...} для створених вузлів/коментарів/фреймів
        self.new_internal_connections_data = []  # Список даних для внутрішніх зв'язків
        self.new_external_connections_refs = []  # Список refs для відновлених зовнішніх зв'язків

        self.setText("Розгрупувати Макрос")

    def redo(self):
        log.debug(f"UngroupMacroCommand redo executing for MacroNode: {self.macro_node_id}")

        # 1. Знайти вузол макросу на сцені
        macro_node = self._find_macro_node()
        if not macro_node or macro_node.scene() != self.scene:
            # Можливо, вузол вже було видалено іншою командою (наприклад, після redo/undo/redo)
            # Перевіряємо, чи збережені дані є
            if not self.ungrouped_items_data:
                log.error(
                    f"UngroupMacroCommand redo: MacroNode {self.macro_node_id} not found on scene and no restore data exists. Command obsolete.")
                self.setObsolete(True)
                return
            else:
                # Спробуємо відновити стан розгрупування
                log.warning(
                    f"UngroupMacroCommand redo: MacroNode {self.macro_node_id} not found, attempting to restore ungrouped state...")
                self._restore_ungrouped_state()
                return

        # 2. Отримати визначення макросу
        macro_definition = self.project_manager.get_macro_data(self.macro_definition_id)
        if not macro_definition:
            log.error(
                f"UngroupMacroCommand redo: Macro definition {self.macro_definition_id} not found. Command obsolete.")
            self.setObsolete(True)
            return

        # 3. Видалити вузол макросу та його зв'язки (зберігаючи дані зв'язків, якщо це перше виконання)
        # Це потрібно зробити ПЕРЕД створенням нових елементів, щоб уникнути конфліктів ID
        log.debug("  Removing original MacroNode and its connections...")
        items_to_remove = {macro_node}
        connections_to_remove = set()
        for socket in macro_node.get_all_sockets():
            connections_to_remove.update(socket.connections)
        items_to_remove.update(connections_to_remove)

        # Використовуємо хелпер з CreateMacroCommand для видалення
        try:
            # Викликаємо з контексту CreateMacroCommand, щоб метод працював
            CreateMacroCommand._remove_items_by_objects(self, items_to_remove)
            log.debug(f"  Removed {len(items_to_remove)} items (MacroNode + connections).")
        except Exception as e:
            log.error(f"  Error removing MacroNode or its connections: {e}", exc_info=True)
            # Спробуємо продовжити, але може бути нестабільний стан
            # self.setObsolete(True); return

        # 4. Підготувати дані для вставки (якщо це перше виконання redo)
        if not self.ungrouped_items_data:
            log.debug("  Preparing data for ungrouped items (first redo execution)...")
            self.ungrouped_items_data = []
            self.new_internal_connections_data = []
            internal_id_map = {}  # {old_internal_id: new_scene_id}
            min_internal_x, min_internal_y = float('inf'), float('inf')

            items_to_process = (
                    [(d, 'node') for d in macro_definition.get('nodes', [])] +
                    [(d, 'comment') for d in macro_definition.get('comments', [])] +
                    [(d, 'frame') for d in macro_definition.get('frames', [])]
            )

            # Знаходимо мінімальні координати всередині макросу для коректного зсуву
            has_valid_pos = False
            for item_data, item_type in items_to_process:
                if item_data.get('node_type') in ['MacroInputNode', 'MacroOutputNode']:
                    continue  # Пропускаємо вузли входу/виходу
                pos = item_data.get('pos')
                if pos:
                    min_internal_x = min(min_internal_x, pos[0])
                    min_internal_y = min(min_internal_y, pos[1])
                    has_valid_pos = True

            if not has_valid_pos:  # Якщо немає елементів з позицією, використовуємо 0,0
                min_internal_x, min_internal_y = 0, 0
                log.warning("  Could not determine minimum internal coordinates, using (0,0) as offset origin.")

            log.debug(f"  Internal offset origin: ({min_internal_x:.1f}, {min_internal_y:.1f})")
            log.debug(
                f"  MacroNode position (paste target origin): ({self.macro_node_pos.x():.1f}, {self.macro_node_pos.y():.1f})")

            for item_data, item_type in items_to_process:
                # Пропускаємо вузли входу/виходу
                if item_data.get('node_type') in ['MacroInputNode', 'MacroOutputNode']:
                    log.debug(f"    Skipping internal {item_data.get('node_type')} ID {item_data.get('id')}")
                    continue

                old_internal_id = item_data.get('id')
                if not old_internal_id:
                    log.warning(f"    Skipping item with missing ID: {item_data}")
                    continue

                new_scene_id = generate_short_id()
                internal_id_map[old_internal_id] = new_scene_id
                log.debug(f"    Mapping internal ID {old_internal_id} to new scene ID {new_scene_id}")

                new_item_data = deepcopy(item_data)
                new_item_data['id'] = new_scene_id

                # Коригуємо позицію
                internal_pos = new_item_data.get('pos', (0, 0))
                # Зсув = (позиція_макронода) + (внутрішня_позиція - мінімальна_внутрішня_позиція)
                new_x = self.macro_node_pos.x() + (internal_pos[0] - min_internal_x)
                new_y = self.macro_node_pos.y() + (internal_pos[1] - min_internal_y)
                new_item_data['pos'] = (new_x, new_y)
                log.debug(f"      Adjusted position: ({new_x:.1f}, {new_y:.1f})")

                self.ungrouped_items_data.append({'type': item_type, 'data': new_item_data})

            # Готуємо дані для внутрішніх зв'язків, використовуючи нові ID
            for conn_data in macro_definition.get('connections', []):
                old_from = conn_data.get('from_node')
                old_to = conn_data.get('to_node')
                new_from = internal_id_map.get(old_from)
                new_to = internal_id_map.get(old_to)

                # Додаємо зв'язок, тільки якщо обидва кінці були розгруповані (не вхід/вихід макросу)
                if new_from and new_to:
                    new_conn_data = deepcopy(conn_data)
                    new_conn_data['from_node'] = new_from
                    new_conn_data['to_node'] = new_to
                    self.new_internal_connections_data.append(new_conn_data)
                    log.debug(
                        f"    Prepared internal connection: {new_from}:{new_conn_data['from_socket']} -> {new_to}:{new_conn_data['to_socket']}")
                else:
                    log.debug(f"    Skipping internal connection involving MacroInput/Output: {old_from} -> {old_to}")

        # 5. Створити та додати внутрішні елементи та зв'язки на сцену
        log.debug("  Creating and adding ungrouped items to the scene...")
        created_nodes_map = {}  # {new_scene_id: node_object}
        created_items_objects = []  # Список всіх створених графічних об'єктів

        view = self.scene.views()[0] if self.scene.views() else None
        if not view: log.error("  Cannot create comments/frames: View not found!")

        for item_info in self.ungrouped_items_data:
            item_data = item_info['data']
            item_type = item_info['type']
            item_id = item_data.get('id')
            created_item = None
            try:
                log.debug(f"    Creating {item_type} with new ID {item_id}")
                if item_type == 'node':
                    created_item = BaseNode.from_data(item_data)
                    created_nodes_map[item_id] = created_item
                elif item_type == 'comment' and view:
                    created_item = CommentItem.from_data(item_data, view)
                elif item_type == 'frame' and view:
                    created_item = FrameItem.from_data(item_data, view)

                if created_item:
                    # Перевірка, чи елемент вже існує (після undo/redo)
                    existing_item = next((i for i in self.scene.items() if hasattr(i, 'id') and i.id == item_id), None)
                    if not existing_item:
                        log.debug(f"      Adding {item_type} {item_id} to scene.")
                        self.scene.addItem(created_item)
                        created_items_objects.append(created_item)
                    elif existing_item == created_item:
                        log.debug(f"      {item_type} {item_id} already on scene (likely after undo/redo).")
                        created_items_objects.append(created_item)  # Додаємо до списку для виділення
                        if item_type == 'node': created_nodes_map[
                            item_id] = created_item  # Переконуємось, що мапа актуальна
                    else:
                        # Ця ситуація не повинна виникати при правильній генерації ID
                        log.warning(
                            f"      Item with ID {item_id} already exists but is a different object. Replacing.")
                        self.scene.removeItem(existing_item)
                        self.scene.addItem(created_item)
                        created_items_objects.append(created_item)
                        if item_type == 'node': created_nodes_map[item_id] = created_item

            except Exception as e:
                log.error(f"    Error creating/adding {item_type} from data {item_data}: {e}", exc_info=True)

        # Створюємо внутрішні зв'язки
        log.debug("  Creating internal connections...")
        for conn_data in self.new_internal_connections_data:
            from_node = created_nodes_map.get(conn_data['from_node'])
            to_node = created_nodes_map.get(conn_data['to_node'])
            if from_node and to_node:
                start_socket = from_node.get_socket(conn_data['from_socket'])
                end_socket = to_node.get_socket(conn_data['to_socket'])
                if start_socket and end_socket:
                    try:
                        # Перевірка, чи з'єднання вже існує (після undo/redo)
                        existing_conn = next((c for c in start_socket.connections if c.end_socket == end_socket), None)
                        if not existing_conn:
                            conn = Connection(start_socket, end_socket)
                            log.debug(
                                f"    Adding internal connection: {from_node.id}:{start_socket.socket_name} -> {to_node.id}:{end_socket.socket_name}")
                            self.scene.addItem(conn)
                            created_items_objects.append(conn)
                        else:
                            log.debug(f"    Internal connection {from_node.id} -> {to_node.id} already exists.")
                            created_items_objects.append(existing_conn)  # Додаємо для виділення

                    except Exception as e:
                        log.error(f"    Error creating/adding internal connection from data {conn_data}: {e}",
                                  exc_info=True)
                else:
                    log.warning(f"    Could not create internal connection, sockets not found for data: {conn_data}")
            else:
                log.warning(f"    Could not create internal connection, nodes not found for data: {conn_data}")

        # 6. Відновити зовнішні зв'язки
        log.debug("  Reconnecting external connections...")
        self.new_external_connections_refs = []  # Очищаємо перед заповненням

        # Вхідні (External -> Internal)
        for inc_data in self.incoming_connections_data:
            external_node = self._find_node_by_id(inc_data['external_node_id'])
            if not external_node:
                log.warning(f"    Cannot reconnect incoming: External node {inc_data['external_node_id']} not found.")
                continue
            external_socket = external_node.get_socket(inc_data['external_socket_name'])
            if not external_socket:
                log.warning(
                    f"    Cannot reconnect incoming: External socket {inc_data['external_socket_name']} on node {external_node.id} not found.")
                continue

            # Знаходимо відповідний внутрішній вузол
            internal_target_node_id = None
            internal_target_socket_name = None
            macro_input_socket_name = inc_data['macro_socket_name']
            for input_def in macro_definition.get('inputs', []):
                if input_def.get('name') == macro_input_socket_name:
                    # Шукаємо вузол, до якого був підключений ВНУТРІШНІЙ вихід MacroInputNode
                    macro_input_node_id = input_def.get('macro_input_node_id')
                    for conn_def in macro_definition.get('connections', []):
                        if conn_def.get('from_node') == macro_input_node_id:
                            old_internal_target_id = conn_def.get('to_node')
                            internal_target_node_id = internal_id_map.get(old_internal_target_id)  # Отримуємо новий ID
                            internal_target_socket_name = conn_def.get('to_socket')
                            log.debug(
                                f"      Mapping incoming '{macro_input_socket_name}' to internal node {internal_target_node_id}:{internal_target_socket_name} (Original internal: {old_internal_target_id})")
                            break
                    break  # Знайшли потрібний input_def

            if not internal_target_node_id or not internal_target_socket_name:
                log.warning(
                    f"    Cannot reconnect incoming: Could not find internal target for macro input '{macro_input_socket_name}'.")
                continue

            internal_node = created_nodes_map.get(internal_target_node_id)
            if not internal_node:
                log.warning(
                    f"    Cannot reconnect incoming: Internal node {internal_target_node_id} (new ID) not found in created map.")
                continue
            internal_socket = internal_node.get_socket(internal_target_socket_name)
            if not internal_socket:
                log.warning(
                    f"    Cannot reconnect incoming: Internal socket {internal_target_socket_name} on node {internal_node.id} not found.")
                continue

            # Створюємо зв'язок
            try:
                # Перевірка на існування (після undo/redo)
                existing_conn = next((c for c in external_socket.connections if c.end_socket == internal_socket), None)
                if not existing_conn:
                    log.debug(
                        f"    Creating incoming connection: {external_node.id}:{external_socket.socket_name} -> {internal_node.id}:{internal_socket.socket_name}")
                    conn = Connection(external_socket, internal_socket)
                    self.scene.addItem(conn)
                    self.new_external_connections_refs.append(
                        {'start_ref': {'node_id': external_node.id, 'socket_name': external_socket.socket_name},
                         'end_ref': {'node_id': internal_node.id, 'socket_name': internal_socket.socket_name}})
                    created_items_objects.append(conn)  # Додаємо для виділення
                else:
                    log.debug(f"    Incoming connection {external_node.id} -> {internal_node.id} already exists.")
                    self.new_external_connections_refs.append(
                        {'start_ref': {'node_id': external_node.id, 'socket_name': external_socket.socket_name},
                         'end_ref': {'node_id': internal_node.id, 'socket_name': internal_socket.socket_name}})
                    created_items_objects.append(existing_conn)  # Додаємо для виділення
            except Exception as e:
                log.error(f"    Error creating incoming connection: {e}", exc_info=True)

        # Вихідні (Internal -> External)
        for out_data in self.outgoing_connections_data:
            external_node = self._find_node_by_id(out_data['external_node_id'])
            if not external_node:
                log.warning(f"    Cannot reconnect outgoing: External node {out_data['external_node_id']} not found.")
                continue
            external_socket = external_node.get_socket(out_data['external_socket_name'])
            if not external_socket:
                log.warning(
                    f"    Cannot reconnect outgoing: External socket {out_data['external_socket_name']} on node {external_node.id} not found.")
                continue

            # Знаходимо відповідний внутрішній вузол
            internal_source_node_id = None
            internal_source_socket_name = None
            macro_output_socket_name = out_data['macro_socket_name']
            for output_def in macro_definition.get('outputs', []):
                if output_def.get('name') == macro_output_socket_name:
                    # Шукаємо вузол, який був підключений до ВНУТРІШНЬОГО входу MacroOutputNode
                    macro_output_node_id = output_def.get('macro_output_node_id')
                    for conn_def in macro_definition.get('connections', []):
                        if conn_def.get('to_node') == macro_output_node_id:
                            old_internal_source_id = conn_def.get('from_node')
                            internal_source_node_id = internal_id_map.get(old_internal_source_id)  # Отримуємо новий ID
                            internal_source_socket_name = conn_def.get('from_socket')
                            log.debug(
                                f"      Mapping outgoing '{macro_output_socket_name}' from internal node {internal_source_node_id}:{internal_source_socket_name} (Original internal: {old_internal_source_id})")
                            break
                    break  # Знайшли потрібний output_def

            if not internal_source_node_id or not internal_source_socket_name:
                log.warning(
                    f"    Cannot reconnect outgoing: Could not find internal source for macro output '{macro_output_socket_name}'.")
                continue

            internal_node = created_nodes_map.get(internal_source_node_id)
            if not internal_node:
                log.warning(
                    f"    Cannot reconnect outgoing: Internal node {internal_source_node_id} (new ID) not found in created map.")
                continue
            internal_socket = internal_node.get_socket(internal_source_socket_name)
            if not internal_socket:
                log.warning(
                    f"    Cannot reconnect outgoing: Internal socket {internal_source_socket_name} on node {internal_node.id} not found.")
                continue

            # Створюємо зв'язок
            try:
                # Перевірка на існування (після undo/redo)
                existing_conn = next((c for c in internal_socket.connections if c.end_socket == external_socket), None)
                if not existing_conn:
                    log.debug(
                        f"    Creating outgoing connection: {internal_node.id}:{internal_socket.socket_name} -> {external_node.id}:{external_socket.socket_name}")
                    conn = Connection(internal_socket, external_socket)
                    self.scene.addItem(conn)
                    self.new_external_connections_refs.append(
                        {'start_ref': {'node_id': internal_node.id, 'socket_name': internal_socket.socket_name},
                         'end_ref': {'node_id': external_node.id, 'socket_name': external_socket.socket_name}})
                    created_items_objects.append(conn)  # Додаємо для виділення
                else:
                    log.debug(f"    Outgoing connection {internal_node.id} -> {external_node.id} already exists.")
                    self.new_external_connections_refs.append(
                        {'start_ref': {'node_id': internal_node.id, 'socket_name': internal_socket.socket_name},
                         'end_ref': {'node_id': external_node.id, 'socket_name': external_socket.socket_name}})
                    created_items_objects.append(existing_conn)  # Додаємо для виділення
            except Exception as e:
                log.error(f"    Error creating outgoing connection: {e}", exc_info=True)

        # 7. Виділити створені елементи
        self.scene.clearSelection()
        for item in created_items_objects:
            if item.scene() == self.scene:  # Перевіряємо, чи елемент все ще на сцені
                item.setSelected(True)
        log.debug(f"  Selected {len(created_items_objects)} ungrouped items.")

        log.info(f"UngroupMacroCommand redo finished successfully for MacroNode {self.macro_node_id}.")

    def undo(self):
        log.debug(f"UngroupMacroCommand undo executing for MacroNode: {self.macro_node_id}")

        # 1. Видалити розгруповані елементи та відновлені зовнішні зв'язки
        log.debug("  Removing ungrouped items and reconnected external connections...")
        items_to_remove_now = set()
        # Знаходимо всі створені елементи за їхніми ID
        ungrouped_ids = {item_info['data'].get('id') for item_info in self.ungrouped_items_data if
                         item_info['data'].get('id')}
        for item in self.scene.items():
            if hasattr(item, 'id') and item.id in ungrouped_ids:
                items_to_remove_now.add(item)
        # Знаходимо внутрішні зв'язки за ID вузлів
        for conn_data in self.new_internal_connections_data:
            from_node = self._find_node_by_id(conn_data['from_node'])
            to_node = self._find_node_by_id(conn_data['to_node'])
            if from_node and to_node:
                start_sock = from_node.get_socket(conn_data['from_socket'])
                if start_sock:
                    conn = next((c for c in start_sock.connections if
                                 c.end_socket == to_node.get_socket(conn_data['to_socket'])), None)
                    if conn: items_to_remove_now.add(conn)
        # Знаходимо зовнішні зв'язки за refs
        for conn_ref in self.new_external_connections_refs:
            conn = CreateMacroCommand._find_connection_by_refs(self, conn_ref['start_ref'], conn_ref['end_ref'])
            if conn: items_to_remove_now.add(conn)

        # Видаляємо знайдені елементи
        try:
            CreateMacroCommand._remove_items_by_objects(self, items_to_remove_now)
            log.debug(f"  Removed {len(items_to_remove_now)} ungrouped items and connections.")
        except Exception as e:
            log.error(f"  Error removing ungrouped items during undo: {e}", exc_info=True)
            # Продовжуємо, щоб спробувати відновити MacroNode

        # 2. Відновити вузол макросу
        log.debug("  Restoring original MacroNode...")
        macro_node = self._find_macro_node()
        if not macro_node:
            try:
                macro_node = MacroNode.from_data(self.macro_node_data)
                # Відновлюємо сокети
                macro_definition = self.project_manager.get_macro_data(self.macro_definition_id)
                if macro_definition:
                    macro_node.update_sockets_from_definition(macro_definition)
                else:
                    log.warning(
                        f"    Cannot restore sockets for MacroNode {self.macro_node_id}: definition {self.macro_definition_id} not found.")
                log.debug(f"    Adding restored MacroNode {self.macro_node_id} to scene.")
                self.scene.addItem(macro_node)
            except Exception as e:
                log.error(f"    Error restoring MacroNode from data: {e}", exc_info=True)
                self.setObsolete(True);
                return  # Критична помилка
        elif macro_node.scene() != self.scene:  # Якщо вузол існує, але не на сцені
            log.debug(f"    MacroNode {self.macro_node_id} exists but not on scene. Adding back.")
            self.scene.addItem(macro_node)

        # 3. Відновити зовнішні зв'язки вузла макросу
        log.debug("  Restoring original external connections to MacroNode...")
        restored_connections = []
        if macro_node:  # Продовжуємо, тільки якщо вузол макросу вдалося відновити/знайти
            # Вхідні
            for inc_data in self.incoming_connections_data:
                external_node = self._find_node_by_id(inc_data['external_node_id'])
                macro_socket = macro_node.get_socket(inc_data['macro_socket_name'])
                if external_node and macro_socket:
                    external_socket = external_node.get_socket(inc_data['external_socket_name'])
                    if external_socket:
                        try:
                            # Перевірка на існування
                            existing_conn = next(
                                (c for c in external_socket.connections if c.end_socket == macro_socket), None)
                            if not existing_conn:
                                log.debug(
                                    f"    Restoring incoming: {external_node.id}:{external_socket.socket_name} -> Macro:{macro_socket.socket_name}")
                                conn = Connection(external_socket, macro_socket)
                                self.scene.addItem(conn)
                                restored_connections.append(conn)
                            else:
                                log.debug(f"    Incoming connection {external_node.id} -> Macro exists.")
                                restored_connections.append(existing_conn)
                        except Exception as e:
                            log.error(f"    Error restoring incoming connection: {e}", exc_info=True)
                    else:
                        log.warning(
                            f"    Cannot restore incoming: External socket {inc_data['external_socket_name']} on {external_node.id} not found.")
                else:
                    log.warning(
                        f"    Cannot restore incoming: External node {inc_data['external_node_id']} or Macro socket {inc_data['macro_socket_name']} not found.")
            # Вихідні
            for out_data in self.outgoing_connections_data:
                external_node = self._find_node_by_id(out_data['external_node_id'])
                macro_socket = macro_node.get_socket(out_data['macro_socket_name'])
                if external_node and macro_socket:
                    external_socket = external_node.get_socket(out_data['external_socket_name'])
                    if external_socket:
                        try:
                            # Перевірка на існування
                            existing_conn = next(
                                (c for c in macro_socket.connections if c.end_socket == external_socket), None)
                            if not existing_conn:
                                log.debug(
                                    f"    Restoring outgoing: Macro:{macro_socket.socket_name} -> {external_node.id}:{external_socket.socket_name}")
                                conn = Connection(macro_socket, external_socket)
                                self.scene.addItem(conn)
                                restored_connections.append(conn)
                            else:
                                log.debug(f"    Outgoing connection Macro -> {external_node.id} exists.")
                                restored_connections.append(existing_conn)
                        except Exception as e:
                            log.error(f"    Error restoring outgoing connection: {e}", exc_info=True)
                    else:
                        log.warning(
                            f"    Cannot restore outgoing: External socket {out_data['external_socket_name']} on {external_node.id} not found.")
                else:
                    log.warning(
                        f"    Cannot restore outgoing: External node {out_data['external_node_id']} or Macro socket {out_data['macro_socket_name']} not found.")

        # 4. Виділити відновлений вузол макросу
        self.scene.clearSelection()
        if macro_node and macro_node.scene() == self.scene:
            macro_node.setSelected(True)
            log.debug(f"  Selected restored MacroNode {self.macro_node_id}.")

        log.info(f"UngroupMacroCommand undo finished for MacroNode {self.macro_node_id}.")

    # --- Допоміжні методи (можна винести в утиліти) ---
    def _find_macro_node(self):
        """Знаходить вузол макросу на сцені за збереженим ID."""
        if not self.macro_node_id or not self.scene: return None
        return next(
            (item for item in self.scene.items() if isinstance(item, MacroNode) and item.id == self.macro_node_id),
            None)

    def _find_node_by_id(self, node_id):
        """Знаходить будь-який BaseNode на сцені за ID."""
        if not node_id or not self.scene: return None
        return next((item for item in self.scene.items() if isinstance(item, BaseNode) and item.id == node_id), None)

    def _restore_ungrouped_state(self):
        """Допоміжний метод для відновлення розгрупованого стану (використовується в redo, якщо MacroNode не знайдено)."""
        log.debug("  Restoring ungrouped state directly...")
        created_nodes_map = {}
        created_items_objects = []
        view = self.scene.views()[0] if self.scene.views() else None

        # Створюємо вузли/коментарі/фрейми
        for item_info in self.ungrouped_items_data:
            item_data = item_info['data'];
            item_type = item_info['type'];
            item_id = item_data.get('id')
            created_item = None
            try:
                if item_type == 'node':
                    created_item = BaseNode.from_data(item_data);
                    created_nodes_map[item_id] = created_item
                elif item_type == 'comment' and view:
                    created_item = CommentItem.from_data(item_data, view)
                elif item_type == 'frame' and view:
                    created_item = FrameItem.from_data(item_data, view)
                if created_item:
                    existing = next((i for i in self.scene.items() if hasattr(i, 'id') and i.id == item_id), None)
                    if not existing: self.scene.addItem(created_item)
                    created_items_objects.append(created_item)
            except Exception as e:
                log.error(f"    Error restoring {item_type} {item_id}: {e}", exc_info=True)

        # Створюємо внутрішні зв'язки
        for conn_data in self.new_internal_connections_data:
            from_node = created_nodes_map.get(conn_data['from_node'])
            to_node = created_nodes_map.get(conn_data['to_node'])
            if from_node and to_node:
                start_sock = from_node.get_socket(conn_data['from_socket'])
                end_sock = to_node.get_socket(conn_data['to_socket'])
                if start_sock and end_sock:
                    try:
                        existing = next((c for c in start_sock.connections if c.end_socket == end_sock), None)
                        if not existing:
                            conn = Connection(start_sock, end_sock); self.scene.addItem(
                                conn); created_items_objects.append(conn)
                        else:
                            created_items_objects.append(existing)
                    except Exception as e:
                        log.error(f"    Error restoring internal conn: {e}", exc_info=True)

        # Відновлюємо зовнішні зв'язки
        macro_definition = self.project_manager.get_macro_data(self.macro_definition_id)  # Потрібен для мапінгу
        if macro_definition:
            # Логіка відновлення зовнішніх зв'язків тут (схожа на redo)
            # ... (пропущено для стислості, але має бути реалізовано аналогічно redo) ...
            # Важливо: використовуємо self.new_external_connections_refs, якщо вони вже були створені
            log.warning(
                "    Restoration of external connections in _restore_ungrouped_state is not fully implemented yet!")  # TODO
            pass

        self.scene.clearSelection()
        for item in created_items_objects:
            if item.scene() == self.scene: item.setSelected(True)
        log.debug("  Restored ungrouped state finished.")

# --- КІНЕЦЬ ДОДАНОГО ---