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

# --- ДОДАНО: MoveItemsCommand ---
class MoveItemsCommand(QUndoCommand):
    """Команда для переміщення одного або більше елементів на сцені."""
    def __init__(self, moved_items_map, parent=None):
        """
        :param moved_items_map: Словник {item: (start_pos, end_pos)}
        """
        super().__init__(parent)
        self.moved_items_map = moved_items_map # Зберігаємо словник
        count = len(moved_items_map)
        self.setText(f"Перемістити {count} елемент{'и' if count > 1 else ''}")
        log.debug(f"MoveItemsCommand initialized for {count} items.")

    def redo(self):
        log.debug(f"Redo MoveItemsCommand: Moving {len(self.moved_items_map)} items to end positions.")
        try:
            for item, (start_pos, end_pos) in self.moved_items_map.items():
                # Перевіряємо, чи елемент все ще існує на сцені
                if item and item.scene():
                    item.setPos(end_pos)
                    log.debug(f"  Moved item {getattr(item, 'id', item)} to {end_pos}")
                else:
                    log.warning(f"  Skipping move redo for item {getattr(item, 'id', item)}: not found on scene.")
            log.debug("MoveItemsCommand redo finished.")
        except Exception as e:
            log.error(f"Error during redo MoveItemsCommand: {e}", exc_info=True)
            # В undo спробуємо повернути ті, що зможемо

    def undo(self):
        log.debug(f"Undo MoveItemsCommand: Moving {len(self.moved_items_map)} items back to start positions.")
        try:
            for item, (start_pos, end_pos) in self.moved_items_map.items():
                # Перевіряємо, чи елемент все ще існує на сцені
                if item and item.scene():
                    item.setPos(start_pos)
                    log.debug(f"  Moved item {getattr(item, 'id', item)} back to {start_pos}")
                else:
                    log.warning(f"  Skipping move undo for item {getattr(item, 'id', item)}: not found on scene.")
            log.debug("MoveItemsCommand undo finished.")
        except Exception as e:
            log.error(f"Error during undo MoveItemsCommand: {e}", exc_info=True)
            # В redo спробуємо повернути ті, що зможемо
# --- КІНЕЦЬ ДОДАНОГО ---

class AddConnectionCommand(QUndoCommand):
    def __init__(self, scene, start_socket, end_socket, parent=None):
        super().__init__(parent)
        self.scene = scene
        self.start_socket_ref = {'node_id': start_socket.parentItem().id, 'socket_name': start_socket.socket_name}
        self.end_socket_ref = {'node_id': end_socket.parentItem().id, 'socket_name': end_socket.socket_name}
        self.connection = None
        start_node_name = start_socket.parentItem().node_name
        end_node_name = end_socket.parentItem().node_name
        self.setText(f"З'єднати '{start_node_name}' та '{end_node_name}'")

    def _find_sockets(self):
        start_node = next((item for item in self.scene.items() if
                           isinstance(item, BaseNode) and item.id == self.start_socket_ref['node_id']), None)
        end_node = next(
            (item for item in self.scene.items() if isinstance(item, BaseNode) and item.id == self.end_socket_ref['node_id']),
            None)
        if start_node and end_node:
            start_socket = start_node.get_socket(self.start_socket_ref['socket_name'])
            end_socket = end_node.get_socket(self.end_socket_ref['socket_name'])
            return start_socket, end_socket
        return None, None

    def redo(self):
        # --- Додано діагностичне логування ---
        log.debug(f"Redo AddConnectionCommand: Connecting {self.start_socket_ref} -> {self.end_socket_ref}")
        start_socket, end_socket = self._find_sockets()
        if not (start_socket and end_socket):
            log.error(f"  Sockets not found during redo.")
            self.setObsolete(True); return

        # --- Перевірка правил з'єднання ---
        is_valid = True
        error_msg = ""
        # 1. Вихід до Входу
        if start_socket.is_output == end_socket.is_output:
            is_valid = False; error_msg = "Неможливо з'єднати два входи або два виходи."
        # 2. Тільки одне з'єднання до Входу
        elif not end_socket.is_output and end_socket.connections:
            # Виняток: дозволяємо підключення до входу МакроВиходу
            if not isinstance(end_socket.parentItem(), MacroOutputNode):
                is_valid = False; error_msg = "Вхідний сокет вже має з'єднання."
        # 3. Деякі Виходи мають тільки одне з'єднання
        elif start_socket.is_output:
            start_node = start_socket.parentItem()
            # Trigger 'out', Decorator 'out_loop'/'out_end'
            if isinstance(start_node, (TriggerNode, DecoratorNode)) and \
               start_socket.socket_name in ('out', 'out_loop', 'out_end') and \
               start_socket.connections:
                is_valid = False; error_msg = f"Вихід '{start_socket.socket_name}' вузла '{start_node.node_type}' може мати лише одне з'єднання."
            # MacroInput 'out'
            elif isinstance(start_node, MacroInputNode) and start_socket.connections:
                is_valid = False; error_msg = "Вихід вузла 'Вхід Макроса' може мати лише одне з'єднання."

        if not is_valid:
            log.warning(f"  Invalid connection attempt: {error_msg}")
            # Показуємо повідомлення користувачу ТІЛЬКИ при першому redo, а не при повторному
            if self.connection is None: # Тільки якщо з'єднання ще не було створено
                 if self.scene.views():
                      main_window = next((v.parent() for v in self.scene.views() if hasattr(v, 'parent') and callable(v.parent)), None)
                      if main_window: main_window.show_status_message(f"Помилка: {error_msg}", 5000, "orange")
            self.setObsolete(True)
            return
        # --- Кінець перевірки правил ---

        if self.connection is None: # Перше виконання
            log.debug(f"  Creating new connection object.")
            self.connection = Connection(start_socket, end_socket)
        else: # Відновлення після undo
            log.debug(f"  Restoring existing connection object.")
            self.connection.start_socket = start_socket
            self.connection.end_socket = end_socket
            # Додаємо з'єднання до сокетів, якщо їх там немає
            if self.connection not in start_socket.connections: start_socket.add_connection(self.connection)
            if self.connection not in end_socket.connections: end_socket.add_connection(self.connection)

        # Додаємо на сцену, якщо його там немає
        if self.connection.scene() != self.scene:
            self.scene.addItem(self.connection)
            log.debug(f"  Connection added to scene.")

        self.connection.update_path()
        log.debug(f"Redo AddConnectionCommand finished successfully.")

    def undo(self):
        # --- Додано діагностичне логування ---
        log.debug(f"Undo AddConnectionCommand: Disconnecting {self.start_socket_ref} -> {self.end_socket_ref}")
        if self.connection:
            start_socket = self.connection.start_socket
            end_socket = self.connection.end_socket
            # Обережно видаляємо посилання
            if start_socket and self.connection in start_socket.connections:
                start_socket.remove_connection(self.connection)
                log.debug(f"  Removed connection from start socket.")
            if end_socket and self.connection in end_socket.connections:
                end_socket.remove_connection(self.connection)
                log.debug(f"  Removed connection from end socket.")
            # Видаляємо зі сцени
            if self.connection.scene() == self.scene:
                self.scene.removeItem(self.connection)
                log.debug("  Connection removed from scene.")
            log.debug("Undo AddConnectionCommand finished.")
        else:
            log.warning("Undo AddConnectionCommand: Connection object is None.")

class RemoveItemsCommand(QUndoCommand):
    def __init__(self, scene, items_to_remove, parent=None):
        super().__init__(parent)
        self.scene = scene
        self.removed_data = [] # Список словників {'type': 'node'/'connection'/'comment'/'frame', 'data': ...}
        self.connections_to_restore = [] # Зберігаємо об'єкти з'єднань
        self.items_to_remove_on_redo = set() # Для зберігання об'єктів для redo

        view = self.scene.views()[0] if self.scene.views() else None

        items_set = set(items_to_remove) # Копія для обробки

        # Спочатку обробляємо вузли, коментарі, фрейми
        items_without_connections = items_set - {item for item in items_set if isinstance(item, Connection)}
        for item in items_without_connections:
             data = None
             item_type = None
             if isinstance(item, BaseNode):
                 item_type = 'node'
                 data = item.to_data()
                 # Знаходимо і додаємо всі підключені з'єднання для видалення разом з вузлом
                 for socket in item.get_all_sockets():
                     items_set.update(socket.connections) # Додаємо з'єднання в загальний набір
             elif isinstance(item, CommentItem):
                 item_type = 'comment'
                 data = item.to_data()
             elif isinstance(item, FrameItem):
                 item_type = 'frame'
                 data = item.to_data()

             if item_type and data:
                 self.removed_data.append({'type': item_type, 'data': data})
             self.items_to_remove_on_redo.add(item) # Зберігаємо об'єкт для redo

        # Тепер обробляємо з'єднання (включаючи ті, що були додані при видаленні вузлів)
        for item in items_set:
            if isinstance(item, Connection):
                # Зберігаємо об'єкт з'єднання для відновлення
                self.connections_to_restore.append(item)
                self.items_to_remove_on_redo.add(item) # Зберігаємо об'єкт для redo

        count = len(self.items_to_remove_on_redo)
        self.setText(f"Видалити {count} елемент{'и' if count > 1 else ''}")

    def redo(self):
        # --- Додано діагностичне логування ---
        log.debug(f"Redo RemoveItemsCommand: Removing {len(self.items_to_remove_on_redo)} items.")
        connections_first = {item for item in self.items_to_remove_on_redo if isinstance(item, Connection)}
        others = self.items_to_remove_on_redo - connections_first

        # Спочатку видаляємо з'єднання
        for conn in connections_first:
             if conn.scene() == self.scene:
                 try:
                     start_sock = conn.start_socket
                     end_sock = conn.end_socket
                     if start_sock: start_sock.remove_connection(conn)
                     if end_sock: end_sock.remove_connection(conn)
                     self.scene.removeItem(conn)
                     log.debug(f"  Removed connection.")
                 except Exception as e:
                     log.error(f"  Error removing connection during redo: {e}", exc_info=True)

        # Потім видаляємо решту
        for item in others:
            if item.scene() == self.scene:
                try:
                    self.scene.removeItem(item)
                    log.debug(f"  Removed item {getattr(item, 'id', item)}.")
                except Exception as e:
                    log.error(f"  Error removing item {getattr(item, 'id', item)} during redo: {e}", exc_info=True)
        log.debug("Redo RemoveItemsCommand finished.")

    def undo(self):
        # --- Додано діагностичне логування ---
        log.debug(f"Undo RemoveItemsCommand: Restoring {len(self.removed_data)} nodes/comments/frames and {len(self.connections_to_restore)} connections.")
        restored_nodes = {}
        view = self.scene.views()[0] if self.scene.views() else None

        # Спочатку відновлюємо вузли, коментарі, фрейми
        for item_info in self.removed_data:
            item_type, item_data = item_info['type'], item_info['data']
            restored_item = None
            item_id = item_data.get('id')
            # Перевіряємо, чи елемент вже існує (можливо, через інші команди undo/redo)
            existing_item = next((i for i in self.scene.items() if hasattr(i, 'id') and i.id == item_id), None)
            if existing_item:
                restored_item = existing_item # Використовуємо існуючий
                log.debug(f"  Using existing item {item_type} ID {item_id}.")
            else: # Створюємо новий
                try:
                    if item_type == 'node':
                        restored_item = BaseNode.from_data(item_data)
                    elif item_type == 'comment' and view:
                        restored_item = CommentItem.from_data(item_data, view)
                    elif item_type == 'frame' and view:
                        restored_item = FrameItem.from_data(item_data, view)

                    if restored_item:
                         self.scene.addItem(restored_item)
                         log.debug(f"  Restored and added {item_type} ID {item_id}.")
                    else:
                         log.warning(f"  Failed to create item {item_type} from data: {item_data}")
                except Exception as e:
                    log.error(f"  Error restoring item {item_type} from data {item_data}: {e}", exc_info=True)

            if item_type == 'node' and restored_item:
                restored_nodes[restored_item.id] = restored_item

        # Потім відновлюємо з'єднання
        for conn in self.connections_to_restore:
            start_node_obj = conn.start_socket.parentItem() if conn.start_socket else None
            end_node_obj = conn.end_socket.parentItem() if conn.end_socket else None

            if not start_node_obj or not end_node_obj:
                 log.warning(f"  Skipping connection restore: Parent node object missing.")
                 continue

            start_node_id = start_node_obj.id
            end_node_id = end_node_obj.id
            start_socket_name = conn.start_socket.socket_name if conn.start_socket else None
            end_socket_name = conn.end_socket.socket_name if conn.end_socket else None

            # Знаходимо відновлені або існуючі вузли
            start_node = restored_nodes.get(start_node_id) or \
                         next((i for i in self.scene.items() if isinstance(i, BaseNode) and i.id == start_node_id), None)
            end_node = restored_nodes.get(end_node_id) or \
                       next((i for i in self.scene.items() if isinstance(i, BaseNode) and i.id == end_node_id), None)

            if start_node and end_node and start_socket_name and end_socket_name:
                start_socket = start_node.get_socket(start_socket_name)
                end_socket = end_node.get_socket(end_socket_name)
                if start_socket and end_socket:
                    # Перевіряємо, чи з'єднання вже існує на сцені
                    if conn.scene() == self.scene:
                         log.debug(f"  Connection between {start_node_id} and {end_node_id} already on scene.")
                         # Переконуємось, що посилання на сокети актуальні
                         if conn not in start_socket.connections: start_socket.add_connection(conn)
                         if conn not in end_socket.connections: end_socket.add_connection(conn)
                    else: # Додаємо з'єднання на сцену
                        try:
                            conn.start_socket = start_socket # Встановлюємо актуальні сокети
                            conn.end_socket = end_socket
                            if conn not in start_socket.connections: start_socket.add_connection(conn)
                            if conn not in end_socket.connections: end_socket.add_connection(conn)
                            self.scene.addItem(conn)
                            conn.update_path()
                            log.debug(f"  Restored and added connection between {start_node_id} and {end_node_id}.")
                        except Exception as e:
                            log.error(f"  Error restoring connection between {start_node_id} and {end_node_id}: {e}", exc_info=True)
                else:
                    log.warning(f"  Sockets not found for connection restore between {start_node_id} and {end_node_id} (Names: {start_socket_name}, {end_socket_name}).")
            else:
                 log.warning(f"  Nodes not found for connection restore between {start_node_id} and {end_node_id}.")
        log.debug("Undo RemoveItemsCommand finished.")

class ChangePropertiesCommand(QUndoCommand):
    def __init__(self, node, old_data, new_data, parent=None):
        super().__init__(parent)
        self.node = node
        self.old_data = old_data # {'name':..., 'desc':..., 'props':..., 'macro_id':...}
        self.new_data = new_data
        self.main_window = next((v.parent() for v in self.node.scene().views() if hasattr(v, 'parent') and callable(v.parent)), None)
        self.setText(f"Змінити властивості '{old_data.get('name', node.id)}'")

    def _apply_data(self, data):
        log.debug(f"Applying data to node {self.node.id}: {data}")
        try:
            # Використовуємо сеттери, якщо вони є, для оновлення UI
            if hasattr(self.node, 'node_name'):
                self.node.node_name = data['name']
            else: # Пряме встановлення, якщо сеттера немає
                self.node._node_name = data['name']
                if hasattr(self.node, 'name_text'): self.node.name_text.setPlainText(data['name'])

            if hasattr(self.node, 'description'):
                self.node.description = data['desc']
            else:
                self.node._description = data['desc']

            # Переконуємось, що властивості - це список кортежів
            props_list = data.get('props', [])
            self.node.properties = list(props_list) if isinstance(props_list, (list, tuple)) else []
            log.debug(f"  Node properties set to: {self.node.properties}")

            # Оновлення macro_id для MacroNode
            if isinstance(self.node, MacroNode):
                 old_macro_id = self.node.macro_id
                 new_macro_id = data.get('macro_id')
                 if old_macro_id != new_macro_id:
                     self.node.macro_id = new_macro_id
                     log.debug(f"  MacroNode {self.node.id} macro_id changed from '{old_macro_id}' to '{new_macro_id}'")
                     # Оновлюємо сокети, якщо прив'язка змінилася
                     if self.main_window and hasattr(self.main_window, 'project_manager'):
                          macro_def = self.main_window.project_manager.get_macro_data(new_macro_id) if new_macro_id else None
                          if macro_def:
                              self.node.update_sockets_from_definition(macro_def)
                          else: # Якщо macro_id став None або визначення не знайдено
                              self.node.clear_sockets() # Очищаємо сокети
                              log.debug(f"  Cleared sockets for MacroNode {self.node.id} due to invalid/missing macro_id.")
                     else:
                         log.warning("  Cannot update MacroNode sockets: MainWindow or ProjectManager not found.")

            # Оновлюємо відображення властивостей
            if self.main_window and hasattr(self.main_window, 'project_manager'):
                 config = self.main_window.project_manager.get_config_data()
                 self.node.update_display_properties(config)
            else:
                 self.node.update_display_properties() # Без конфігурації
            log.debug(f"  Node display properties updated.")

            # Оновлюємо панель властивостей, якщо цей вузол вибраний
            if self.main_window and self.main_window.current_selected_node == self.node:
                 log.debug(f"  Triggering properties panel UI update.")
                 self.main_window._update_properties_panel_ui()

        except Exception as e:
            log.error(f"Error applying data to node {self.node.id}: {e}", exc_info=True)


    def redo(self):
        # --- Додано діагностичне логування ---
        log.debug(f"Redo ChangePropertiesCommand for node {self.node.id}")
        self._apply_data(self.new_data)
        log.debug("Redo ChangePropertiesCommand finished.")

    def undo(self):
        # --- Додано діагностичне логування ---
        log.debug(f"Undo ChangePropertiesCommand for node {self.node.id}")
        self._apply_data(self.old_data)
        log.debug("Undo ChangePropertiesCommand finished.")

class AddCommentCommand(QUndoCommand):
    def __init__(self, scene, position, view, parent=None):
        super().__init__(parent)
        self.scene = scene
        self.position = position
        self.view = view
        self.comment_id = None # Згенеруємо в redo
        self.comment_data = {'text': "Новий коментар", 'pos': (position.x(), position.y()), 'size': (150, 80)}
        self.setText("Додати коментар")

    def redo(self):
        log.debug(f"Redo AddCommentCommand at {self.position}")
        comment = None
        if self.comment_id: # Шукаємо існуючий після undo
             comment = next((item for item in self.scene.items() if isinstance(item, CommentItem) and item.id == self.comment_id), None)

        if not comment: # Створюємо новий або відновлюємо дані
             try:
                 # Передаємо view в from_data
                 comment = CommentItem.from_data(self.comment_data, self.view)
                 if not self.comment_id: # Генеруємо ID тільки при першому redo
                      self.comment_id = comment.id
                 else: # Встановлюємо збережений ID при повторному redo
                      comment.id = self.comment_id
                 self.comment_data['id'] = self.comment_id # Зберігаємо ID в даних
                 log.debug(f"  Created/Restored comment object {self.comment_id}")
             except Exception as e:
                 log.error(f"  Error creating comment from data {self.comment_data}: {e}", exc_info=True)
                 self.setObsolete(True); return

        # Додаємо на сцену, якщо його там немає
        if comment and comment.scene() != self.scene:
             self.scene.addItem(comment)
             log.debug(f"  Added comment {self.comment_id} to scene.")

        if comment:
             self.scene.clearSelection()
             comment.setSelected(True)
             log.debug("Redo AddCommentCommand finished.")
        else:
             log.error("Failed to create or find comment in redo.")
             self.setObsolete(True)


    def undo(self):
        log.debug(f"Undo AddCommentCommand for comment {self.comment_id}")
        comment = next((item for item in self.scene.items() if isinstance(item, CommentItem) and item.id == self.comment_id), None)
        if comment and comment.scene() == self.scene:
            try:
                # Зберігаємо актуальний текст перед видаленням
                self.comment_data['text'] = comment.text
                self.comment_data['size'] = (comment._width, comment._height)
                self.scene.removeItem(comment)
                log.debug(f"  Removed comment {self.comment_id} from scene.")
            except Exception as e:
                log.error(f"  Error removing comment {self.comment_id}: {e}", exc_info=True)
        else:
            log.warning(f"  Comment {self.comment_id} not found on scene for undo.")
        log.debug("Undo AddCommentCommand finished.")

class ResizeCommand(QUndoCommand):
    def __init__(self, item, old_dims, new_dims, parent=None):
        super().__init__(parent)
        self.item = item # CommentItem or FrameItem
        self.item_id = item.id
        self.old_dims = old_dims # (width, height)
        self.new_dims = new_dims
        item_type = "Коментар" if isinstance(item, CommentItem) else "Фрейм" if isinstance(item, FrameItem) else "Елемент"
        self.setText(f"Змінити розмір '{item_type}'")

    def _find_item(self):
        if not self.item or not self.item.scene():
             # Спробуємо знайти за ID, якщо об'єкт втрачено
             item_class = CommentItem if "Коментар" in self.text() else FrameItem if "Фрейм" in self.text() else None
             if item_class:
                  self.item = next((i for i in self.scene().items() if isinstance(i, item_class) and i.id == self.item_id), None)
        return self.item

    def redo(self):
        log.debug(f"Redo ResizeCommand for item {self.item_id} to {self.new_dims}")
        item = self._find_item()
        if item and hasattr(item, 'set_dimensions'):
             item.set_dimensions(*self.new_dims)
        else:
             log.warning(f"Item {self.item_id} not found or has no set_dimensions method for redo.")
             self.setObsolete(True)

    def undo(self):
        log.debug(f"Undo ResizeCommand for item {self.item_id} to {self.old_dims}")
        item = self._find_item()
        if item and hasattr(item, 'set_dimensions'):
             item.set_dimensions(*self.old_dims)
        else:
             log.warning(f"Item {self.item_id} not found or has no set_dimensions method for undo.")
             # Не робимо obsolete тут, можливо, з'явиться в redo

class AddFrameCommand(QUndoCommand):
    def __init__(self, scene, items_to_group, parent=None):
        super().__init__(parent)
        self.scene = scene
        self.grouped_items_ids = {item.id for item in items_to_group if hasattr(item, 'id')}
        self.frame_id = None # Генеруємо в redo
        self.frame_data = None # Дані для створення фрейму
        self.setText(f"Сгрупувати {len(items_to_group)} елемент{'и' if len(items_to_group)!=1 else ''}")

    def redo(self):
        log.debug(f"Redo AddFrameCommand for {len(self.grouped_items_ids)} items.")
        frame = None
        if self.frame_id: # Шукаємо існуючий
             frame = next((item for item in self.scene.items() if isinstance(item, FrameItem) and item.id == self.frame_id), None)

        if not frame: # Створюємо новий або відновлюємо дані
            if not self.frame_data: # Перше виконання redo
                items_to_group_now = {item for item in self.scene.items() if hasattr(item, 'id') and item.id in self.grouped_items_ids}
                if not items_to_group_now:
                     log.warning("No items to group found in redo."); self.setObsolete(True); return

                # Розрахунок геометрії фрейму
                bounding_rect = QRectF()
                for item in items_to_group_now:
                     item_rect = item.sceneBoundingRect()
                     bounding_rect = bounding_rect.united(item_rect) if bounding_rect.isValid() else item_rect
                if not bounding_rect.isValid():
                     log.warning("Cannot calculate bounding rect for grouping."); self.setObsolete(True); return

                padding = 20
                frame_rect = bounding_rect.adjusted(-padding, -padding - FrameItem(text="").header_height, padding, padding)
                frame_pos = frame_rect.topLeft()
                frame_size = (frame_rect.width(), frame_rect.height())
                frame_text = f"Група {len(items_to_group_now)}"
                self.frame_data = {'text': frame_text, 'pos': (frame_pos.x(), frame_pos.y()), 'size': frame_size}
                if not self.frame_id: self.frame_id = generate_short_id()
                self.frame_data['id'] = self.frame_id
                log.debug(f"Calculated frame data: ID={self.frame_id}, Rect={frame_rect}")

            # Створення/відновлення фрейму
            view = self.scene.views()[0] if self.scene.views() else None
            try:
                frame = FrameItem.from_data(self.frame_data, view)
                frame.id = self.frame_id # Переконуємось, що ID правильний
                log.debug(f"Created/Restored frame object {self.frame_id}")
            except Exception as e:
                log.error(f"Error creating frame from data {self.frame_data}: {e}", exc_info=True)
                self.setObsolete(True); return

        # Додаємо фрейм на сцену, якщо його немає
        if frame and frame.scene() != self.scene:
            self.scene.addItem(frame)
            log.debug(f"Added frame {self.frame_id} to scene.")

        if frame:
            self.scene.clearSelection()
            frame.setSelected(True)
            log.debug("Redo AddFrameCommand finished.")
        else:
            log.error("Failed to create or find frame in redo.")
            self.setObsolete(True)

    def undo(self):
        log.debug(f"Undo AddFrameCommand for frame {self.frame_id}")
        frame = next((item for item in self.scene.items() if isinstance(item, FrameItem) and item.id == self.frame_id), None)
        if frame and frame.scene() == self.scene:
            try:
                # Зберігаємо актуальні дані перед видаленням
                self.frame_data = frame.to_data()
                self.scene.removeItem(frame)
                log.debug(f"Removed frame {self.frame_id} from scene.")
                # Відновлюємо виділення згрупованих елементів
                self.scene.clearSelection()
                items_to_select = {item for item in self.scene.items() if hasattr(item, 'id') and item.id in self.grouped_items_ids}
                for item in items_to_select: item.setSelected(True)
                log.debug(f"Restored selection for {len(items_to_select)} items.")
            except Exception as e:
                log.error(f"Error removing frame {self.frame_id}: {e}", exc_info=True)
        else:
            log.warning(f"Frame {self.frame_id} not found on scene for undo.")
        log.debug("Undo AddFrameCommand finished.")

class UngroupFrameCommand(QUndoCommand):
    def __init__(self, scene, frame_to_ungroup, parent=None):
        super().__init__(parent)
        self.scene = scene
        self.frame_data = frame_to_ungroup.to_data() # Зберігаємо дані фрейму
        self.frame_id = frame_to_ungroup.id
        self.contained_items_ids = {item.id for item in frame_to_ungroup.get_contained_nodes() if hasattr(item, 'id')}
        self.setText(f"Розгрупувати фрейм '{frame_to_ungroup.text}'")

    def redo(self):
        log.debug(f"Redo UngroupFrameCommand for frame {self.frame_id}")
        frame = next((item for item in self.scene.items() if isinstance(item, FrameItem) and item.id == self.frame_id), None)
        if frame and frame.scene() == self.scene:
            try:
                # Зберігаємо актуальні дані (хоча вони вже є в self.frame_data)
                self.frame_data = frame.to_data()
                self.scene.removeItem(frame)
                log.debug(f"Removed frame {self.frame_id} from scene.")
                # Виділяємо елементи, що були всередині
                self.scene.clearSelection()
                items_to_select = {item for item in self.scene.items() if hasattr(item, 'id') and item.id in self.contained_items_ids}
                for item in items_to_select: item.setSelected(True)
                log.debug(f"Selected {len(items_to_select)} previously contained items.")
            except Exception as e:
                log.error(f"Error removing frame {self.frame_id}: {e}", exc_info=True)
                self.setObsolete(True) # Якщо не вдалося видалити, команда недійсна
        else:
            log.warning(f"Frame {self.frame_id} not found on scene for redo.")
            # Не робимо obsolete тут, бо undo може його відновити
        log.debug("Redo UngroupFrameCommand finished.")

    def undo(self):
        log.debug(f"Undo UngroupFrameCommand for frame {self.frame_id}")
        # Перевіряємо, чи фрейм вже існує
        frame = next((item for item in self.scene.items() if isinstance(item, FrameItem) and item.id == self.frame_id), None)
        if not frame:
            # Створюємо фрейм з даних
            view = self.scene.views()[0] if self.scene.views() else None
            try:
                frame = FrameItem.from_data(self.frame_data, view)
                frame.id = self.frame_id # Переконуємось, що ID правильний
                log.debug(f"Restored frame object {self.frame_id}")
            except Exception as e:
                log.error(f"Error restoring frame from data {self.frame_data}: {e}", exc_info=True)
                # Не робимо obsolete, бо redo може спрацювати
                return

        # Додаємо фрейм на сцену, якщо його немає
        if frame.scene() != self.scene:
            self.scene.addItem(frame)
            log.debug(f"Added frame {self.frame_id} back to scene.")

        # Виділяємо відновлений фрейм
        self.scene.clearSelection()
        frame.setSelected(True)
        log.debug("Undo UngroupFrameCommand finished.")


class PasteCommand(QUndoCommand):
    def __init__(self, scene, clipboard_xml_string, paste_pos, view, current_edit_mode, parent=None):
        super().__init__(parent)
        self.scene = scene
        self.clipboard_xml_string = clipboard_xml_string
        self.paste_pos = paste_pos
        self.view = view # Потрібен для створення CommentItem/FrameItem
        self.current_edit_mode = current_edit_mode
        self.pasted_item_ids = []
        self.setText("Вставити елементи")
        log.debug(f"PasteCommand initialized at pos {paste_pos}")

    def redo(self):
        log.debug(f"Redo PasteCommand: Pasting items at {self.paste_pos}")
        try:
            root_xml = ET.fromstring(self.clipboard_xml_string.encode('utf-8'))
            nodes_xml = root_xml.find("nodes")
            connections_xml = root_xml.find("connections")
            comments_xml = root_xml.find("comments")
            frames_xml = root_xml.find("frames")

            items_data = []
            if nodes_xml is not None: items_data.extend([(BaseNode.data_from_xml(el), 'node') for el in nodes_xml])
            if comments_xml is not None: items_data.extend([(CommentItem.data_from_xml(el), 'comment') for el in comments_xml])
            if frames_xml is not None: items_data.extend([(FrameItem.data_from_xml(el), 'frame') for el in frames_xml])

            if not items_data: log.warning("No nodes/comments/frames found in clipboard data."); return

            # Розрахунок зсуву
            min_x, min_y = float('inf'), float('inf')
            has_pos = False
            for data, _ in items_data:
                pos = data.get('pos')
                if pos:
                     min_x, min_y = min(min_x, pos[0]), min(min_y, pos[1]); has_pos = True
            if not has_pos: min_x, min_y = 0, 0 # Якщо немає позицій, використовуємо 0,0
            offset = self.paste_pos - QPointF(min_x, min_y)
            log.debug(f"Calculated paste offset: {offset}")

            # Створення та додавання елементів
            pasted_items = []
            old_to_new_id_map = {}
            self.scene.clearSelection()

            for data, item_type in items_data:
                old_id = data.get('id')
                new_id = generate_short_id() # Генеруємо новий ID
                if old_id: old_to_new_id_map[old_id] = new_id
                data['id'] = new_id # Замінюємо ID

                # Зміщуємо позицію
                old_pos = data.get('pos', (0,0))
                data['pos'] = (old_pos[0] + offset.x(), old_pos[1] + offset.y())

                # Перевірка режиму редагування
                if item_type == 'node':
                    node_class_name = data.get('node_type')
                    if self.current_edit_mode == EDIT_MODE_MACRO and node_class_name in ['TriggerNode', 'MacroNode']:
                        log.warning(f"Skipping paste of {node_class_name} in macro mode.")
                        continue
                    if node_class_name == 'TriggerNode' and any(isinstance(i, TriggerNode) for i in self.scene.items()):
                         log.warning(f"Skipping paste of TriggerNode: already exists.")
                         continue

                # Створення елемента
                item = None
                try:
                    if item_type == 'node':
                        item = BaseNode.from_data(data)
                    elif item_type == 'comment':
                        item = CommentItem.from_data(data, self.view)
                    elif item_type == 'frame':
                         item = FrameItem.from_data(data, self.view)

                    if item:
                         self.scene.addItem(item)
                         item.setSelected(True)
                         pasted_items.append(item)
                         self.pasted_item_ids.append(new_id) # Зберігаємо ID для undo
                         log.debug(f"  Pasted {item_type} {old_id} as {new_id} at {item.pos()}")
                    else:
                         log.warning(f"  Failed to create item from data: {data}")
                except Exception as e:
                    log.error(f"  Error creating/adding pasted item {item_type} from data {data}: {e}", exc_info=True)


            # Створення з'єднань
            if connections_xml is not None:
                pasted_nodes_map = {item.id: item for item in pasted_items if isinstance(item, BaseNode)}
                for conn_el in connections_xml:
                     conn_data = Connection.data_from_xml(conn_el)
                     old_from_id = conn_data.get('from_node')
                     old_to_id = conn_data.get('to_node')
                     new_from_id = old_to_new_id_map.get(old_from_id)
                     new_to_id = old_to_new_id_map.get(old_to_id)

                     if new_from_id and new_to_id: # Тільки якщо обидва кінці вставлено
                         start_node = pasted_nodes_map.get(new_from_id)
                         end_node = pasted_nodes_map.get(new_to_id)
                         if start_node and end_node:
                             start_socket = start_node.get_socket(conn_data['from_socket'])
                             end_socket = end_node.get_socket(conn_data['to_socket'])
                             if start_socket and end_socket:
                                 try:
                                     conn = Connection(start_socket, end_socket)
                                     self.scene.addItem(conn)
                                     # З'єднання не виділяємо і не зберігаємо ID для undo
                                     log.debug(f"  Pasted connection {old_from_id} -> {old_to_id} as {new_from_id} -> {new_to_id}")
                                 except Exception as e:
                                     log.error(f"  Error creating/adding pasted connection from {new_from_id} to {new_to_id}: {e}", exc_info=True)
                             else:
                                 log.warning(f"  Sockets not found for pasted connection between {new_from_id} and {new_to_id}")
                         else:
                             log.warning(f"  Nodes not found in map for pasted connection between {new_from_id} and {new_to_id}")
                     else:
                          log.debug(f"  Skipping connection {old_from_id} -> {old_to_id}: one or both ends not pasted.")

            log.info(f"PasteCommand redo finished. Pasted {len(self.pasted_item_ids)} items.")

        except ET.XMLSyntaxError:
            log.error("PasteCommand redo failed: Invalid XML in clipboard.")
            self.setObsolete(True)
        except Exception as e:
            log.error(f"PasteCommand redo failed: {e}", exc_info=True)
            # Спробувати видалити частково вставлені елементи?
            self._remove_pasted_items() # Викликаємо очищення
            self.setObsolete(True)

    def undo(self):
        log.debug(f"Undo PasteCommand: Removing {len(self.pasted_item_ids)} pasted items.")
        self._remove_pasted_items()
        log.debug("Undo PasteCommand finished.")

    def _remove_pasted_items(self):
        """Допоміжний метод для видалення вставлених елементів."""
        items_to_remove = []
        connections_to_remove = set()
        id_set = set(self.pasted_item_ids)

        for item in self.scene.items():
             # Знаходимо вставлені вузли/коментарі/фрейми за ID
             if hasattr(item, 'id') and item.id in id_set:
                 items_to_remove.append(item)
                 # Якщо це вузол, знаходимо підключені з'єднання, які теж були вставлені
                 if isinstance(item, BaseNode):
                      for socket in item.get_all_sockets():
                           for conn in socket.connections:
                                other_node = conn.start_socket.parentItem() if conn.end_socket == socket else conn.end_socket.parentItem()
                                # Видаляємо з'єднання, тільки якщо інший кінець теж був вставлений
                                if other_node and hasattr(other_node, 'id') and other_node.id in id_set:
                                     connections_to_remove.add(conn)

        # Використовуємо хелпер з RemoveItemsCommand для видалення
        all_to_remove = set(items_to_remove) | connections_to_remove
        try:
             # Викликаємо метод як статичний або з об'єкта RemoveItemsCommand?
             # Поки що просто імітуємо логіку видалення тут
             cons = {i for i in all_to_remove if isinstance(i, Connection)}
             others = all_to_remove - cons
             for c in cons:
                  if c.scene() == self.scene:
                       if c.start_socket: c.start_socket.remove_connection(c)
                       if c.end_socket: c.end_socket.remove_connection(c)
                       self.scene.removeItem(c)
             for o in others:
                  if o.scene() == self.scene: self.scene.removeItem(o)
             log.debug(f"  Removed {len(all_to_remove)} items.")
        except Exception as e:
            log.error(f"Error removing pasted items: {e}", exc_info=True)

        self.pasted_item_ids = [] # Очищаємо список після видалення
