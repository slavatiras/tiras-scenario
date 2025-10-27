import uuid
import logging  # Додано
from copy import deepcopy  # Додано для копіювання структур даних
from lxml import etree as ET
from PyQt6.QtGui import QUndoCommand
from PyQt6.QtCore import QPointF, QRectF
# Додано імпорти для запиту імені макросу
from PyQt6.QtWidgets import QInputDialog, QLineEdit, QMessageBox # Додано QMessageBox

# Імпортуємо DecoratorNode для перевірки в AddConnectionCommand
from nodes import (BaseNode, Connection, CommentItem, FrameItem, TriggerNode, DecoratorNode, MacroNode,
                   MacroInputNode, MacroOutputNode, NODE_REGISTRY, generate_short_id)

# --- ИСПРАВЛЕНО: Импортируем константы из нового файла ---
from constants import EDIT_MODE_SCENARIO, EDIT_MODE_MACRO

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
        self.main_window = main_window # Зберігаємо для подальшого використання

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

        if self.main_window and hasattr(self.main_window, 'project_manager'): # Перевіряємо наявність менеджера
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
        log.debug(f"Redo AddNodeAndConnectCommand: Adding node {self.new_node.id} ({self.node_type_name}) and connecting from {self.start_node_id}:{self.start_socket_name}")
        try:
            # Перевіряємо, чи вузол вже на сцені (після undo)
            if self.new_node.scene() != self.scene:
                self.scene.addItem(self.new_node)
                log.debug(f"  Node {self.new_node.id} added to scene.")
            else:
                 log.debug(f"  Node {self.new_node.id} already on scene.")

            start_node = next(
                (item for item in self.scene.items() if isinstance(item, BaseNode) and item.id == self.start_node_id), None)
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
            else: # Відновлення з'єднання після undo
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
                 try: self.scene.removeItem(self.connection)
                 except Exception: pass
            if self.new_node and self.new_node.scene() == self.scene:
                 try: self.scene.removeItem(self.new_node)
                 except Exception: pass
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


# ... (решта команд без змін, окрім CreateMacroCommand) ...


class AddCommentCommand(QUndoCommand):
    def __init__(self, scene, position, view, parent=None):
        super().__init__(parent)
        self.scene = scene
        self.position = position
        # Зберігаємо дані замість об'єкта
        self.comment_data = {'text': "Коментар", 'pos': (position.x(), position.y()), 'size': (200, 100)}
        self.comment_item = None  # Створюється в redo
        self.view = view  # Потрібен для створення CommentItem

    def redo(self):
        if self.comment_item is None:
            # Використовуємо from_data для створення
            self.comment_item = CommentItem.from_data(self.comment_data, self.view)
        # Додаємо на сцену, якщо ще не там
        if self.comment_item.scene() != self.scene:
            self.scene.addItem(self.comment_item)
        self.scene.clearSelection()
        self.comment_item.setSelected(True)

    def undo(self):
        if self.comment_item and self.comment_item.scene() == self.scene:
            # Зберігаємо поточний текст перед видаленням
            self.comment_data['text'] = self.comment_item.text
            self.comment_data['size'] = (self.comment_item._width, self.comment_item._height)
            self.scene.removeItem(self.comment_item)


class ResizeCommand(QUndoCommand):
    def __init__(self, item, old_dims, new_dims, parent=None):
        super().__init__(parent)
        self.item = item
        # Зберігаємо ID елемента
        self.item_id = item.id if hasattr(item, 'id') else None
        self.item_class = type(item)  # Зберігаємо тип елемента
        self.old_dims = old_dims
        self.new_dims = new_dims
        item_type = "коментаря" if isinstance(item, CommentItem) else "фрейму" if isinstance(item,
                                                                                             FrameItem) else "елемента"
        self.setText(f"Змінити розмір {item_type}")

    def _find_item(self):
        """Знаходить елемент на сцені за ID."""
        # Додаємо перевірку на існування сцени у self.item
        if not self.item_id or not self.item or not self.item.scene(): return None
        return next((i for i in self.item.scene().items() if
                     isinstance(i, self.item_class) and hasattr(i, 'id') and i.id == self.item_id), None)

    def redo(self):
        item = self._find_item()
        if item and hasattr(item, 'set_dimensions'):
            item.set_dimensions(self.new_dims[0], self.new_dims[1])
        elif not item:
            log.warning(f"ResizeCommand redo: Item {self.item_id} not found.")
            self.setObsolete(True)

    def undo(self):
        item = self._find_item()
        if item and hasattr(item, 'set_dimensions'):
            item.set_dimensions(self.old_dims[0], self.old_dims[1])
        elif not item:
            # Не робимо obsolete, бо елемент міг бути видалений іншою командою
            log.warning(f"ResizeCommand undo: Item {self.item_id} not found.")


class AlignNodesCommand(QUndoCommand):
    def __init__(self, nodes, mode, parent=None):
        super().__init__(parent)
        # Зберігаємо ID вузлів
        self.node_ids = [node.id for node in nodes]
        self.mode = mode
        # Зберігаємо старі позиції за ID
        self.old_positions = {node.id: node.pos() for node in nodes}
        self.scene = nodes[0].scene() if nodes else None
        self.setText("Вирівняти вузли")

    def _find_nodes(self):
        """Знаходить вузли на сцені за їх ID."""
        nodes = []
        if not self.scene: return nodes
        id_set = set(self.node_ids)
        for item in self.scene.items():
            if isinstance(item, BaseNode) and item.id in id_set:
                nodes.append(item)
        return nodes

    def redo(self):
        nodes = self._find_nodes()
        if len(nodes) < 2:
            log.warning("AlignNodesCommand redo: Not enough valid nodes found.")
            self.setObsolete(True)  # Робимо команду недійсною
            return

        # Determine the target based on the mode using found nodes
        if self.mode == 'left':
            target_node = min(nodes, key=lambda n: n.sceneBoundingRect().left())
            align_pos = target_node.sceneBoundingRect().left()
            for node in nodes:
                if node is not target_node: node.setX(align_pos)
        elif self.mode == 'right':
            target_node = max(nodes, key=lambda n: n.sceneBoundingRect().right())
            align_pos = target_node.sceneBoundingRect().right()
            for node in nodes:
                if node is not target_node: node.setX(align_pos - node.sceneBoundingRect().width())
        elif self.mode == 'h_center':
            avg_center_x = sum(n.sceneBoundingRect().center().x() for n in nodes) / len(nodes)
            for node in nodes: node.setX(avg_center_x - node.sceneBoundingRect().width() / 2)
        elif self.mode == 'top':
            target_node = min(nodes, key=lambda n: n.sceneBoundingRect().top())
            align_pos = target_node.sceneBoundingRect().top()
            for node in nodes:
                if node is not target_node: node.setY(align_pos)
        elif self.mode == 'bottom':
            target_node = max(nodes, key=lambda n: n.sceneBoundingRect().bottom())
            align_pos = target_node.sceneBoundingRect().bottom()
            for node in nodes:
                if node is not target_node: node.setY(align_pos - node.sceneBoundingRect().height())
        elif self.mode == 'v_center':
            avg_center_y = sum(n.sceneBoundingRect().center().y() for n in nodes) / len(nodes)
            for node in nodes: node.setY(avg_center_y - node.sceneBoundingRect().height() / 2)

    def undo(self):
        nodes_map = {node.id: node for node in self._find_nodes()}
        for node_id, pos in self.old_positions.items():
            node = nodes_map.get(node_id)
            if node:  # Застосовуємо скасування тільки якщо вузол знайдено
                node.setPos(pos)


class RemoveItemsCommand(QUndoCommand):
    def __init__(self, scene, items, parent=None):
        super().__init__(parent)
        self.scene = scene
        self.items_data = []  # Stores {'type': str, 'data': dict} for restoration

        items_to_remove_set = set(items)
        connections_to_remove_explicitly = set()

        # Find nodes among selected items
        nodes_to_remove = {item for item in items_to_remove_set if isinstance(item, BaseNode)}
        node_ids_to_remove = {node.id for node in nodes_to_remove}

        # Add connections attached to selected nodes
        for node in nodes_to_remove:
            for socket in node.get_all_sockets():
                # Make a copy of connections list because we might modify it indirectly
                for conn in list(socket.connections):
                    items_to_remove_set.add(conn)

        # Also find connections where BOTH ends are being removed (implicitly selected)
        # Need to iterate over ALL connections in the scene
        all_connections_in_scene = [item for item in scene.items() if isinstance(item, Connection)]
        for conn in all_connections_in_scene:
            # Check sockets and parent items carefully
            start_socket = conn.start_socket
            end_socket = conn.end_socket
            start_node = start_socket.parentItem() if start_socket else None
            end_node = end_socket.parentItem() if end_socket else None
            if start_node and end_node and start_node.id in node_ids_to_remove and end_node.id in node_ids_to_remove:
                items_to_remove_set.add(conn)

        # Store data for undo, separating nodes, connections, and others
        for item in items_to_remove_set:
            item_data = {}
            item_type = None
            try:  # Додаємо try-except навколо to_data
                if isinstance(item, BaseNode):
                    item_data = item.to_data()
                    item_type = 'node'
                elif isinstance(item, Connection):
                    # Ensure connection data is valid before storing
                    conn_data = item.to_data()
                    if conn_data:  # Only store if nodes exist and data is valid
                        item_data = conn_data
                        item_type = 'connection'
                    else:
                        log.warning(
                            f"RemoveItemsCommand: Skipping invalid connection during data saving (obj: {item}).")
                        connections_to_remove_explicitly.add(item)  # Mark for explicit removal
                elif isinstance(item, CommentItem):  # Handle CommentItem
                    item_data = item.to_data()
                    item_type = 'comment'
                elif isinstance(item, FrameItem):  # Handle FrameItem
                    item_data = item.to_data()
                    item_type = 'frame'

                if item_type and item_data:  # Only store if valid type and data
                    self.items_data.append({'type': item_type, 'data': item_data})
            except Exception as e:
                log.error(f"Error calling to_data() for item {item}: {e}", exc_info=True)

        # Keep track of the actual item objects for removal in redo
        self.items_to_remove_objects = items_to_remove_set
        self.connections_to_remove_explicitly = connections_to_remove_explicitly
        self.setText(f"Видалити {len(items_to_remove_set)} елемент(и)")
        log.debug(f"RemoveItemsCommand initialized. Stored data for {len(self.items_data)} items.")

    def redo(self):
        log.debug(f"RemoveItemsCommand redo: Removing {len(self.items_to_remove_objects)} items.")
        # Remove connections first
        connections_removed = set()
        for item in self.items_to_remove_objects:
            if isinstance(item, Connection):
                if item.scene() == self.scene:
                    try:
                        if item.start_socket: item.start_socket.remove_connection(item)
                        if item.end_socket: item.end_socket.remove_connection(item)
                        self.scene.removeItem(item)
                        connections_removed.add(item)
                    except Exception as e:
                        log.error(f"Error removing connection {item} in redo: {e}", exc_info=True)

        # Remove nodes and containers
        for item in self.items_to_remove_objects:
            if item not in connections_removed:  # Перевіряємо, чи ще не видалено
                if item.scene() == self.scene:
                    try:
                        self.scene.removeItem(item)
                    except Exception as e:
                        log.error(f"Error removing item {item} in redo: {e}", exc_info=True)

        # Explicitly remove connections that were invalid during data saving
        for conn in self.connections_to_remove_explicitly:
            if conn.scene() == self.scene:
                log.debug(f" Explicitly removing invalid connection {conn}")
                try:
                    if conn.start_socket: conn.start_socket.remove_connection(conn)
                    if conn.end_socket: conn.end_socket.remove_connection(conn)
                    self.scene.removeItem(conn)
                except Exception as e:
                    log.error(f"Error explicitly removing connection {conn} in redo: {e}", exc_info=True)

    def undo(self):
        log.debug(f"RemoveItemsCommand undo: Restoring {len(self.items_data)} items.")
        nodes_map = {}  # Map ID -> restored Node object
        created_items_map = {}  # Map tuple(item_data) -> restored item object

        # Get the view for creating Comment/Frame items
        view = self.scene.views()[0] if self.scene.views() else None
        if not view:
            log.error("RemoveItemsCommand undo: Cannot restore Comment/Frame items - view not found.")
            # We can still try to restore nodes and connections

        # Add nodes, comments, and frames back first
        for item_info in self.items_data:
            item_type = item_info['type']
            item_data = item_info['data']
            restored_item = None

            try:
                if item_type == 'node':
                    restored_item = BaseNode.from_data(item_data)
                    nodes_map[restored_item.id] = restored_item
                elif item_type == 'comment' and view:
                    restored_item = CommentItem.from_data(item_data, view)
                elif item_type == 'frame' and view:
                    restored_item = FrameItem.from_data(item_data, view)

                if restored_item:
                    item_id = getattr(restored_item, 'id', None)
                    existing_item = next((i for i in self.scene.items() if hasattr(i, 'id') and i.id == item_id),
                                         None) if item_id else None
                    if not existing_item:
                        self.scene.addItem(restored_item)
                    elif existing_item != restored_item:
                        log.warning(f"Item {item_id} already exists on scene during undo. Replacing.")
                        self.scene.removeItem(existing_item)
                        self.scene.addItem(restored_item)
                        if item_type == 'node': nodes_map[item_id] = restored_item  # Update map if replaced

                    # Use frozenset for dictionary key
                    created_items_map[frozenset(item_data.items())] = restored_item

            except Exception as e:
                log.error(f" Error restoring item {item_type} from data {item_data}: {e}", exc_info=True)

        # Restore connections
        for item_info in self.items_data:
            if item_info['type'] == 'connection':
                conn_data = item_info['data']
                from_node_id = conn_data.get('from_node')
                to_node_id = conn_data.get('to_node')

                # Ensure IDs exist before proceeding
                if not from_node_id or not to_node_id:
                    log.warning(
                        f"RemoveItemsCommand undo: Skipping connection restore due to missing node ID: {conn_data}")
                    continue

                from_node = nodes_map.get(from_node_id)
                to_node = nodes_map.get(to_node_id)

                # Try finding nodes on scene if not in restored map (maybe they weren't removed)
                if not from_node: from_node = next(
                    (item for item in self.scene.items() if isinstance(item, BaseNode) and item.id == from_node_id),
                    None)
                if not to_node: to_node = next(
                    (item for item in self.scene.items() if isinstance(item, BaseNode) and item.id == to_node_id), None)

                if from_node and to_node:
                    start_socket = from_node.get_socket(conn_data['from_socket'])
                    to_socket_name = conn_data.get('to_socket', 'in')
                    end_socket = to_node.get_socket(to_socket_name)

                    if start_socket and end_socket:
                        already_exists = any(
                            existing_conn.end_socket == end_socket for existing_conn in start_socket.connections)
                        if not already_exists:
                            try:
                                conn = Connection(start_socket, end_socket)
                                self.scene.addItem(conn)
                                created_items_map[frozenset(conn_data.items())] = conn
                            except Exception as e:
                                log.error(f"Error creating/adding connection in undo: {e}", exc_info=True)
                    else:
                        log.warning(f"RemoveItemsCommand undo: Could not find sockets for connection {conn_data}")
                else:
                    log.warning(f"RemoveItemsCommand undo: Could not find nodes for connection {conn_data}")


class MoveItemsCommand(QUndoCommand):
    def __init__(self, items_map, parent=None):
        super().__init__(parent)
        self.scene = list(items_map.keys())[0].scene() if items_map else None
        # Зберігаємо ID та старі/нові позиції
        self.items_pos_data = []
        for item, (old_pos, new_pos) in items_map.items():
            if hasattr(item, 'id'):  # Зберігаємо тільки для елементів з ID
                self.items_pos_data.append({'id': item.id, 'class': type(item), 'old_pos': old_pos, 'new_pos': new_pos})
        self.setText("Перемістити елементи")

    def _find_item(self, item_id, item_class):
        """Знаходить елемент на сцені за ID та класом."""
        if not self.scene: return None
        return next(
            (i for i in self.scene.items() if isinstance(i, item_class) and hasattr(i, 'id') and i.id == item_id), None)

    def _apply_pos(self, pos_key):
        if not self.scene: return
        for data in self.items_pos_data:
            item = self._find_item(data['id'], data['class'])
            if item:  # Застосовуємо позицію тільки якщо елемент знайдено
                item.setPos(data[pos_key])
            else:
                log.warning(f"MoveItemsCommand: Item {data['id']} not found during position apply.")

    def redo(self):
        self._apply_pos('new_pos')

    def undo(self):
        self._apply_pos('old_pos')


class AddConnectionCommand(QUndoCommand):
    def __init__(self, scene, start_socket, end_socket, parent=None):
        super().__init__(parent)
        self.scene = scene
        # Зберігаємо посилання на сокети через ID вузлів та імена сокетів
        start_node = start_socket.parentItem()
        end_node = end_socket.parentItem()
        # Додаємо перевірку, що батьківські вузли існують
        if not start_node or not end_node:
            log.error("AddConnectionCommand init: Invalid start or end node for sockets.")
            self.start_socket_ref = None
            self.end_socket_ref = None
        else:
            self.start_socket_ref = {'node_id': start_node.id, 'socket_name': start_socket.socket_name}
            self.end_socket_ref = {'node_id': end_node.id, 'socket_name': end_socket.socket_name}

        self.connection_id = None
        self.connection = None  # Connection object created in redo
        self.setText("Додати з'єднання")
        log.debug(f"AddConnectionCommand initialized: {self.start_socket_ref} -> {self.end_socket_ref}")

    def _find_socket(self, ref):
        """Helper to find socket by reference, returns None if not found."""
        if not self.scene or not ref: return None
        node = next((item for item in self.scene.items() if isinstance(item, BaseNode) and item.id == ref['node_id']),
                    None)
        if node:
            socket = node.get_socket(ref['socket_name'])
            return socket
        return None

    def redo(self):
        if not self.start_socket_ref or not self.end_socket_ref:
            log.error("AddConnectionCommand redo: Command initialized with invalid socket refs.")
            self.setObsolete(True)
            return

        log.debug("AddConnectionCommand redo: Finding sockets...")
        start_socket = self._find_socket(self.start_socket_ref)
        end_socket = self._find_socket(self.end_socket_ref)

        log.debug(
            f"  Start socket found: {start_socket is not None} (Node: {self.start_socket_ref['node_id']}, Socket: {self.start_socket_ref['socket_name']})")
        log.debug(
            f"  End socket found: {end_socket is not None} (Node: {self.end_socket_ref['node_id']}, Socket: {self.end_socket_ref['socket_name']})")

        if not (start_socket and end_socket):
            log.error("AddConnectionCommand redo: Could not find sockets. Command obsolete.")
            self.setObsolete(True)
            return

        try:
            log.debug("AddConnectionCommand redo: Validating connection...")
            if start_socket.is_output == end_socket.is_output:
                log.error("AddConnectionCommand redo: Invalid connection direction. Command obsolete.")
                self.setObsolete(True)
                return
            if not start_socket.is_output:  # Ensure start is output
                start_socket, end_socket = end_socket, start_socket
                self.start_socket_ref, self.end_socket_ref = self.end_socket_ref, self.start_socket_ref

            log.debug(
                f"  Checking input socket {end_socket.socket_name} on {end_socket.parent_node.id}. Connections: {len(end_socket.connections)}")
            if len(end_socket.connections) > 0 and not (self.connection and self.connection in end_socket.connections):
                log.error("AddConnectionCommand redo: Input socket already has a connection. Command obsolete.")
                self.setObsolete(True)
                return

            output_node = start_socket.parentItem()
            log.debug(
                f"  Checking output socket {start_socket.socket_name} on {output_node.id} ({type(output_node).__name__}). Connections: {len(start_socket.connections)}")
            if isinstance(output_node, (TriggerNode, DecoratorNode)):
                restricted_sockets = ('out', 'out_loop', 'out_end')
                if start_socket.socket_name in restricted_sockets and len(start_socket.connections) > 0 and not (
                        self.connection and self.connection in start_socket.connections):
                    log.error(
                        f"AddConnectionCommand redo: Output socket {start_socket.socket_name} on {type(output_node).__name__} already has a connection. Command obsolete.")
                    self.setObsolete(True)
                    return

            log.debug("AddConnectionCommand redo: Creating/Restoring connection object...")
            if self.connection is None:
                log.debug(
                    f"  Creating new Connection: {start_socket.socket_name} ({start_socket.parent_node.id}) -> {end_socket.socket_name} ({end_socket.parent_node.id})")
                self.connection = Connection(start_socket, end_socket)
            else:
                log.debug(
                    f"  Restoring existing Connection: {start_socket.socket_name} ({start_socket.parent_node.id}) -> {end_socket.socket_name} ({end_socket.parent_node.id})")
                self.connection.start_socket = start_socket
                self.connection.end_socket = end_socket
                if self.connection not in start_socket.connections:
                    log.debug(f"  Adding connection back to start socket {start_socket.socket_name}")
                    start_socket.add_connection(self.connection)
                if self.connection not in end_socket.connections:
                    log.debug(f"  Adding connection back to end socket {end_socket.socket_name}")
                    end_socket.add_connection(self.connection)

            log.debug("AddConnectionCommand redo: Adding connection to scene...")
            if self.connection.scene() != self.scene:
                self.scene.addItem(self.connection)

            log.debug("AddConnectionCommand redo: Updating connection path...")
            self.connection.update_path()
            self.scene.clearSelection()
            log.debug("AddConnectionCommand redo: Finished successfully.")

        except Exception as e:
            log.error(f"AddConnectionCommand redo: Unexpected error: {e}", exc_info=True)
            self.setObsolete(True)
            if self.connection and self.connection.scene() == self.scene:
                log.debug("  Attempting to remove partially added connection due to error.")
                if self.connection.start_socket: self.connection.start_socket.remove_connection(self.connection)
                if self.connection.end_socket: self.connection.end_socket.remove_connection(self.connection)
                self.scene.removeItem(self.connection)

    def undo(self):
        log.debug("AddConnectionCommand undo: Starting...")
        start_socket = self._find_socket(self.start_socket_ref)
        end_socket = self._find_socket(self.end_socket_ref)
        connection_to_remove = None

        log.debug(f"  Undo: Start socket found: {start_socket is not None}")
        log.debug(f"  Undo: End socket found: {end_socket is not None}")

        if self.connection:
            if self.connection.start_socket == start_socket and self.connection.end_socket == end_socket:
                connection_to_remove = self.connection
                log.debug("  Undo: Found connection using self.connection reference.")
            else:
                log.warning("  Undo: self.connection sockets do not match found sockets. Searching on scene...")
                if start_socket:
                    connection_to_remove = next(
                        (conn for conn in start_socket.connections if conn.end_socket == end_socket), None)

        if not connection_to_remove and start_socket:
            log.debug("  Undo: Searching for connection via start_socket...")
            connection_to_remove = next((conn for conn in start_socket.connections if conn.end_socket == end_socket),
                                        None)

        if connection_to_remove:
            log.debug(f"  Undo: Found connection to remove: {connection_to_remove}")
            start_sock = connection_to_remove.start_socket
            end_sock = connection_to_remove.end_socket
            log.debug(
                f"  Undo: Removing connection from sockets {start_sock.socket_name if start_sock else '?'} and {end_sock.socket_name if end_sock else '?'}")
            if start_sock: start_sock.remove_connection(connection_to_remove)
            if end_sock: end_sock.remove_connection(connection_to_remove)
            if connection_to_remove.scene() == self.scene:
                log.debug("  Undo: Removing connection from scene.")
                self.scene.removeItem(connection_to_remove)
        else:
            log.warning("AddConnectionCommand undo: Connection object not found on scene or sockets invalid.")

        log.debug("AddConnectionCommand undo: Finished.")


class ChangePropertiesCommand(QUndoCommand):
    def __init__(self, node, old_data, new_data, parent=None):
        super().__init__(parent)
        self.node_id = node.id
        self.scene = node.scene()
        self.old_data = deepcopy(old_data)  # Глибоке копіювання
        self.new_data = deepcopy(new_data)  # Глибоке копіювання
        self.setText("Змінити властивості")
        self.main_window = next((v.parent() for v in self.scene.views() if hasattr(v, 'parent') and callable(v.parent)),
                                None)

    def _find_node(self):
        """Finds the node on the scene by ID."""
        if not self.scene: return None
        return next((item for item in self.scene.items() if isinstance(item, BaseNode) and item.id == self.node_id),
                    None)

    def _apply_data(self, data):
        node = self._find_node()
        if not node:
            log.error(f"ChangePropertiesCommand: Node {self.node_id} not found on scene.")
            self.setObsolete(True)
            return

        log.debug(f"Applying data to node {self.node_id}: {data}") # DIAGNOSTIC
        try:
            # Застосовуємо дані
            node.node_name = data['name']
            node.description = data['desc']
            # Ensure properties are copied
            node.properties = list(data['props'])  # Create new list
            # Apply macro_id if present
            if 'macro_id' in data and hasattr(node, 'macro_id'):
                # Check if macro_id actually changed
                if node.macro_id != data['macro_id']:
                    log.debug(f"  Macro ID changed from {node.macro_id} to {data['macro_id']}") # DIAGNOSTIC
                    node.macro_id = data['macro_id']
                    # Update sockets if it's a MacroNode and macro_id changed
                    if isinstance(node, MacroNode) and self.main_window and hasattr(self.main_window, 'project_manager'):
                        # --- ВИКОРИСТАННЯ project_manager ---
                        new_macro_data = self.main_window.project_manager.get_macro_data(node.macro_id)
                        # --- КІНЕЦЬ ---
                        if new_macro_data:
                            log.debug("    Updating MacroNode sockets from new definition.") # DIAGNOSTIC
                            node.update_sockets_from_definition(new_macro_data)
                        else: # Macro definition not found (or cleared)
                            log.warning(f"    Macro definition {node.macro_id} not found. Clearing sockets.") # DIAGNOSTIC
                            node.clear_sockets() # Or set to default? Clear is safer.
                    elif isinstance(node, MacroNode):
                         log.warning("    Cannot update MacroNode sockets: MainWindow or ProjectManager not found.")

            # Update display and potentially UI
            if self.main_window and hasattr(self.main_window, 'project_manager'):
                # --- ВИКОРИСТАННЯ project_manager ---
                config_data = self.main_window.project_manager.get_config_data()
                # --- КІНЕЦЬ ---
                node.update_display_properties(config_data)
                log.debug("  Node display properties updated.") # DIAGNOSTIC
                # Update properties panel if this node is selected
                if self.main_window.current_selected_node == node:
                    log.debug("  Triggering UI update for properties panel.") # DIAGNOSTIC
                    self.main_window._update_properties_panel_ui()  # Trigger UI update
            else:
                log.warning("ChangePropertiesCommand: Could not find MainWindow or ProjectManager for display update.")

        except Exception as e:
            log.error(f"Error applying data to node {self.node_id}: {e}", exc_info=True)
            self.setObsolete(True) # Робимо команду недійсною при помилці


    def redo(self):
        log.debug(f"Redo ChangePropertiesCommand for node {self.node_id}.")
        self._apply_data(self.new_data)

    def undo(self):
        log.debug(f"Undo ChangePropertiesCommand for node {self.node_id}.")
        self._apply_data(self.old_data)


class PasteCommand(QUndoCommand):
    def __init__(self, scene, clipboard_text, position, view, current_edit_mode, parent=None):
        super().__init__(parent)
        self.scene = scene
        self.view = view  # Needed for Comment/Frame creation
        self.clipboard_text = clipboard_text
        self.position = position
        self.current_edit_mode = current_edit_mode  # Store edit mode
        self.pasted_items_data = []  # Store item DATA, not objects
        self.pasted_connections_data = []  # Store connection DATA separately
        self.created_item_ids = {}  # Map old ID -> new ID
        self.created_connection_refs = []  # List of {'start_ref':..., 'end_ref':...}
        self.setText("Вставити елементи")
        self.main_window = next((v.parent() for v in self.scene.views() if hasattr(v, 'parent') and callable(v.parent)),
                                None)

    def redo(self):
        created_nodes = {}  # Map new ID -> new Node object (for this redo call)
        created_others = {}  # Map new ID -> new Comment/Frame object

        # If data not prepared, parse clipboard
        if not self.pasted_items_data and not self.pasted_connections_data:

            self.scene = self.view.scene() if self.view else None
            if not self.scene:
                log.error("PasteCommand: Scene not found via view.")
                self.setObsolete(True); return

            pasted_input_node_found = False
            pasted_output_node_found = False
            scene_has_input = any(isinstance(item, MacroInputNode) for item in self.scene.items())
            scene_has_output = any(isinstance(item, MacroOutputNode) for item in self.scene.items())
            log.debug(f"PasteCommand redo: Scene check: has_input={scene_has_input}, has_output={scene_has_output}")

            try:
                clipboard_root = ET.fromstring(self.clipboard_text.encode('utf-8'))
                min_x, min_y = float('inf'), float('inf')
                elements_to_process = []

                for type_key, find_path in [('node', 'nodes/node'), ('comment', 'comments/comment'), ('frame', 'frames/frame')]:
                    container_xml = clipboard_root.find(find_path.split('/')[0])
                    if container_xml is not None:
                        for el in container_xml.findall(find_path.split('/')[1]):
                            x = float(el.get("x", 0)); y = float(el.get("y", 0))
                            min_x, min_y = min(min_x, x), min(min_y, y)
                            elements_to_process.append((el, type_key))

                if min_x == float('inf'): log.warning("PasteCommand: No valid elements found."); self.setObsolete(True); return

                ref_pos = QPointF(min_x, min_y)
                self.created_item_ids = {}

                for el, item_type in elements_to_process:
                    item_data = None
                    if item_type == 'node': item_data = BaseNode.data_from_xml(el)
                    elif item_type == 'comment': item_data = CommentItem.data_from_xml(el)
                    elif item_type == 'frame': item_data = FrameItem.data_from_xml(el)
                    if not item_data: continue

                    node_class_name = item_data.get('node_type') if item_type == 'node' else None
                    if self.current_edit_mode == EDIT_MODE_MACRO:
                        if node_class_name in ['TriggerNode', 'MacroNode']: log.debug(f"Skipping {node_class_name} paste into macro."); continue
                        if node_class_name == 'MacroInputNode':
                            if scene_has_input or pasted_input_node_found: log.warning("Skipping duplicate MacroInputNode."); continue
                            pasted_input_node_found = True
                        if node_class_name == 'MacroOutputNode':
                            if scene_has_output or pasted_output_node_found: log.warning("Skipping duplicate MacroOutputNode."); continue
                            pasted_output_node_found = True
                    elif self.current_edit_mode == EDIT_MODE_SCENARIO:
                        if node_class_name in ['MacroInputNode', 'MacroOutputNode']: log.debug(f"Skipping {node_class_name} paste into scenario."); continue

                    old_id = item_data['id']; new_id = generate_short_id()
                    item_data['id'] = new_id
                    original_pos = QPointF(*item_data['pos'])
                    offset_pos = self.position + (original_pos - ref_pos)
                    item_data['pos'] = (offset_pos.x(), offset_pos.y())
                    self.pasted_items_data.append({'type': item_type, 'data': item_data, 'old_id': old_id})
                    self.created_item_ids[old_id] = new_id

                connections_xml = clipboard_root.find("connections")
                if connections_xml is not None and any(item['type'] == 'node' for item in self.pasted_items_data):
                    for conn_el in connections_xml:
                        conn_data = Connection.data_from_xml(conn_el)
                        if conn_data['from_node'] in self.created_item_ids and conn_data['to_node'] in self.created_item_ids:
                             self.pasted_connections_data.append(conn_data)
            except Exception as e: log.error(f"PasteCommand: Error parsing clipboard: {e}", exc_info=True); self.setObsolete(True); return

        # --- Create and add items ---
        config = None
        if self.main_window and hasattr(self.main_window, 'project_manager'):
             config = self.main_window.project_manager.get_config_data()

        for item_info in self.pasted_items_data:
             item_data = item_info['data']; new_id = item_data['id']; item_type = item_info['type']
             created_item = None
             existing_item = next((item for item in self.scene.items() if hasattr(item, 'id') and item.id == new_id), None)
             if existing_item:
                 if item_type == 'node': created_nodes[new_id] = existing_item
                 else: created_others[new_id] = existing_item
                 if existing_item.scene() != self.scene: self.scene.addItem(existing_item)
                 log.debug(f"Paste redo: Using existing {item_type} {new_id}"); continue
             try:
                 if item_type == 'node':
                     created_item = BaseNode.from_data(item_data)
                     created_item.update_display_properties(config); created_nodes[new_id] = created_item
                 elif item_type == 'comment': created_item = CommentItem.from_data(item_data, self.view); created_others[new_id] = created_item
                 elif item_type == 'frame': created_item = FrameItem.from_data(item_data, self.view); created_others[new_id] = created_item
                 if created_item: self.scene.addItem(created_item)
             except Exception as e: log.error(f"Paste failed create {item_type}: {item_data}. Error: {e}", exc_info=True)

        # Create connections
        self.created_connection_refs = []
        for conn_data in self.pasted_connections_data:
             new_from_id = self.created_item_ids.get(conn_data['from_node'])
             new_to_id = self.created_item_ids.get(conn_data['to_node'])
             if new_from_id and new_to_id:
                  from_node, to_node = created_nodes.get(new_from_id), created_nodes.get(new_to_id)
                  if from_node and to_node:
                       from_socket = from_node.get_socket(conn_data['from_socket'])
                       to_socket = to_node.get_socket(conn_data.get('to_socket', 'in'))
                       if from_socket and to_socket:
                           already_exists = any(conn.end_socket == to_socket for conn in from_socket.connections)
                           conn_ref_data = {'start_ref': {'node_id': new_from_id, 'socket_name': from_socket.socket_name}, 'end_ref': {'node_id': new_to_id, 'socket_name': to_socket.socket_name}}
                           self.created_connection_refs.append(conn_ref_data)
                           if not already_exists: conn = Connection(from_socket, to_socket); self.scene.addItem(conn)
                       else: log.warning(f"Paste redo: Sockets not found: {conn_data}")
                  else: log.warning(f"Paste redo: Nodes not found: {conn_data} (IDs: {new_from_id}, {new_to_id})")
             else: log.warning(f"Paste redo: Old node IDs not mapped: {conn_data}")

        self.scene.clearSelection()
        for item in {**created_nodes, **created_others}.values(): item.setSelected(True)

    def undo(self):
         created_items_on_scene = {}
         for old_id, new_id in self.created_item_ids.items():
              item = next((item for item in self.scene.items() if hasattr(item, 'id') and item.id == new_id), None)
              if item: created_items_on_scene[new_id] = item
         for conn_ref in self.created_connection_refs:
              start_node = created_items_on_scene.get(conn_ref['start_ref']['node_id'])
              end_node = created_items_on_scene.get(conn_ref['end_ref']['node_id'])
              if isinstance(start_node, BaseNode) and isinstance(end_node, BaseNode):
                   start_socket = start_node.get_socket(conn_ref['start_ref']['socket_name'])
                   end_socket = end_node.get_socket(conn_ref['end_ref']['socket_name'])
                   connection_to_remove = next((conn for conn in start_socket.connections if conn.end_socket == end_socket), None) if start_socket else None
                   if connection_to_remove:
                        if connection_to_remove.start_socket: connection_to_remove.start_socket.remove_connection(connection_to_remove)
                        if connection_to_remove.end_socket: connection_to_remove.end_socket.remove_connection(connection_to_remove)
                        if connection_to_remove.scene() == self.scene: self.scene.removeItem(connection_to_remove)
         for item in created_items_on_scene.values():
              if item.scene() == self.scene: self.scene.removeItem(item)


class AddFrameCommand(QUndoCommand):
    def __init__(self, scene, items, parent=None):
        super().__init__(parent)
        self.scene = scene
        self.item_ids = [item.id for item in items if hasattr(item, 'id')]
        self.frame_data = None
        self.frame_id = None
        self.setText("Сгрупувати в фрейм")

    def _find_items(self):
        items = []
        if not self.scene: return items
        id_set = set(self.item_ids)
        for item in self.scene.items():
            if hasattr(item, 'id') and item.id in id_set:
                items.append(item)
        return items

    def _find_frame(self):
         if not self.frame_id or not self.scene: return None
         return next((i for i in self.scene.items() if isinstance(i, FrameItem) and i.id == self.frame_id), None)

    def redo(self):
        frame = self._find_frame()
        if frame:
             if frame.scene() != self.scene: self.scene.addItem(frame)
             self.scene.clearSelection(); frame.setSelected(True); return
        items = self._find_items()
        if not items: log.warning("AddFrame redo: No valid items found."); self.setObsolete(True); return
        bounding_rect = QRectF()
        for item in items: bounding_rect = bounding_rect.united(item.sceneBoundingRect()) if bounding_rect.isValid() else item.sceneBoundingRect()
        padding = 20; frame_rect = bounding_rect.adjusted(-padding, -padding, padding, padding)
        frame_pos = frame_rect.topLeft(); frame_size = (frame_rect.width(), frame_rect.height())
        self.frame_id = generate_short_id()
        self.frame_data = {'id': self.frame_id, 'text': "Новая группа", 'pos': (frame_pos.x(), frame_pos.y()), 'size': frame_size}
        view = self.scene.views()[0] if self.scene.views() else None
        frame = FrameItem.from_data(self.frame_data, view)
        self.scene.addItem(frame); self.scene.clearSelection(); frame.setSelected(True)

    def undo(self):
        frame = self._find_frame()
        if frame and frame.scene() == self.scene: self.scene.removeItem(frame)
        items = self._find_items(); self.scene.clearSelection()
        for item in items:
            if item.scene() == self.scene: item.setSelected(True)


class UngroupFrameCommand(QUndoCommand):
    def __init__(self, scene, frame, parent=None):
        super().__init__(parent)
        self.scene = scene
        self.frame_data = frame.to_data() # Store frame data for undo
        self.frame_id = frame.id # Зберігаємо ID
        self.setText("Разгрупувати фрейм")

    def _find_frame(self):
         if not self.frame_id or not self.scene: return None
         return next((i for i in self.scene.items() if isinstance(i, FrameItem) and i.id == self.frame_id), None)

    def redo(self):
        frame = self._find_frame()
        if frame and frame.scene() == self.scene: self.scene.removeItem(frame)
        self.scene.clearSelection()

    def undo(self):
        frame = self._find_frame()
        if not frame:
             view = self.scene.views()[0] if self.scene.views() else None
             frame = FrameItem.from_data(self.frame_data, view); self.scene.addItem(frame)
        elif frame.scene() != self.scene: self.scene.addItem(frame)
        self.scene.clearSelection()
        if frame: frame.setSelected(True)


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
             self.project_manager = None # Або генерувати виключення
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
                      self.setObsolete(True); return
            if macro_node:
                self.new_external_connections_refs = self._reconnect_external_connections(macro_node, self.external_connections_data)

            self.main_window.update_macros_list() # Залишається, оновлює UI
            log.debug("Macro creation redo finished.")
            return

        log.debug("First execution: Creating macro...")
        selected_items = {item for item in self.scene.items() if hasattr(item, 'id') and item.id in self.initial_selected_ids}
        if not selected_items:
             log.warning("CreateMacroCommand redo: Initial selected items not found."); self.setObsolete(True); return

        try:
            macro_data, external_connections_info = self._create_macro_definition_and_analyze(selected_items)
            if not macro_data: self.setObsolete(True); return
            self.macro_id = macro_data['id']
            self.macro_data_backup = deepcopy(macro_data) # Зберігаємо копію для undo/redo
            self.external_connections_data = external_connections_info
            # --- ВИПРАВЛЕННЯ: Додаємо визначення макросу в менеджер ---
            self.project_manager.add_or_update_macro(self.macro_id, macro_data)
            log.debug(f"Added macro definition {self.macro_id} to ProjectManager.")
            # --- КІНЕЦЬ ВИПРАВЛЕННЯ ---
        except Exception as e: log.error(f"Error during macro definition: {e}", exc_info=True); self.setObsolete(True); return

        self.removed_items_data = []
        items_to_remove = set(selected_items) # Починаємо з вибраних
        internal_connections = self._find_internal_connections(selected_items)
        items_to_remove.update(internal_connections) # Додаємо внутрішні з'єднання
        items_to_remove.update({info['original_conn'] for info in external_connections_info}) # Додаємо зовнішні з'єднання

        for item in items_to_remove:
            item_type = None
            if isinstance(item, BaseNode): item_type = 'node'
            elif isinstance(item, Connection): item_type = 'connection'
            elif isinstance(item, CommentItem): item_type = 'comment'
            elif isinstance(item, FrameItem): item_type = 'frame'
            if item_type:
                try:
                    item_data = item.to_data()
                    if item_data: self.removed_items_data.append({'type': item_type, 'data': item_data})
                except Exception as e: log.error(f"Error getting data for removed item {item}: {e}", exc_info=True)

        self._remove_items_by_objects(items_to_remove)
        macro_node = self._create_and_add_macro_node(macro_data)
        if not macro_node: return

        self.new_external_connections_refs = self._reconnect_external_connections(macro_node, external_connections_info)
        self.main_window.update_macros_list() # Оновлюємо UI
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

        self.main_window.update_macros_list() # Оновлюємо UI
        log.debug("CreateMacroCommand undo finished.")


    # --- Допоміжні методи ---

    def _find_macro_node(self):
         if not self.macro_node_id or not self.scene: return None
         return next((item for item in self.scene.items() if isinstance(item, MacroNode) and item.id == self.macro_node_id), None)

    def _find_connection_by_refs(self, start_ref, end_ref):
         start_node = next((item for item in self.scene.items() if isinstance(item, BaseNode) and item.id == start_ref['node_id']), None)
         end_node = next((item for item in self.scene.items() if isinstance(item, BaseNode) and item.id == end_ref['node_id']), None)
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
                   except Exception as e: log.error(f"Error removing conn: {e}", exc_info=True)
         for item in others:
              if item.scene() == self.scene:
                   try: self.scene.removeItem(item)
                   except Exception as e: log.error(f"Error removing item {getattr(item, 'id', item)}: {e}", exc_info=True)


    def _remove_restored_items(self):
         log.debug(f"Removing {len(self.removed_items_data)} restored items...")
         items_to_remove_now = set()
         restored_ids = {item_data['data'].get('id') for item_data in self.removed_items_data if item_data['data'].get('id')}
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
            if item_id: existing_item = next((i for i in self.scene.items() if hasattr(i, 'id') and i.id == item_id), None)
            if existing_item: log.debug(f"Item {item_id} exists. Using existing."); restored_item = existing_item;
            else:
                try:
                    log.debug(f"Attempt restore {item_type} ID {item_id}")
                    if item_type == 'node': restored_item = BaseNode.from_data(item_data)
                    elif item_type == 'comment' and view: restored_item = CommentItem.from_data(item_data, view)
                    elif item_type == 'frame' and view: restored_item = FrameItem.from_data(item_data, view)
                    if restored_item: log.debug(f"Adding restored {item_type} {item_id}"); self.scene.addItem(restored_item)
                    else: log.warning(f"Failed create {item_type} ID {item_id}")
                except Exception as e: log.error(f" Err restore {item_type} data {item_data}: {e}", exc_info=True)
            if item_type == 'node' and restored_item: restored_nodes[item_id] = restored_item

        for item_info in self.removed_items_data:
            if item_info['type'] == 'connection':
                conn_data = item_info['data']
                from_node_id, to_node_id = conn_data.get('from_node'), conn_data.get('to_node')
                log.debug(f"Attempt restore conn {from_node_id} -> {to_node_id}")
                if not from_node_id or not to_node_id: continue
                from_node = restored_nodes.get(from_node_id) or next((i for i in self.scene.items() if isinstance(i, BaseNode) and i.id == from_node_id), None)
                to_node = restored_nodes.get(to_node_id) or next((i for i in self.scene.items() if isinstance(i, BaseNode) and i.id == to_node_id), None)
                if from_node and to_node:
                    start_socket = from_node.get_socket(conn_data['from_socket'])
                    end_socket = to_node.get_socket(conn_data.get('to_socket', 'in'))
                    if start_socket and end_socket:
                        already_exists = any(conn.end_socket == end_socket for conn in start_socket.connections)
                        if not already_exists:
                            try: conn = Connection(start_socket, end_socket); self.scene.addItem(conn); conn.update_path(); log.debug(f" OK restore conn {from_node_id} -> {to_node_id}")
                            except Exception as e: log.error(f"Err create/add conn undo: {e}", exc_info=True)
                        else: log.debug(f"Conn {from_node_id} -> {to_node_id} exists.")
                    else: log.warning(f" Sockets not found for restore conn: {conn_data}")
                else: log.warning(f" Nodes not found for restore conn: {conn_data}")


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
            f"Макрос {macros_count + 1}" # Використовуємо отриману кількість
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
        min_x, min_y = float('inf'), float('inf'); bounding_rect = QRectF()

        try:
            items_to_normalize = list(selected_nodes) + list(selected_comments) + list(selected_frames)
            if not items_to_normalize: return None, None
            for item in items_to_normalize:
                item_rect = item.sceneBoundingRect(); item_pos = item.pos()
                bounding_rect = bounding_rect.united(item_rect) if bounding_rect.isValid() else item_rect
                min_x, min_y = min(min_x, item_pos.x()), min(min_y, item_pos.y())
                new_id, old_id = generate_short_id(), item.id
                old_id_to_new_id[old_id] = new_id
                item_data = item.to_data(); item_data['id'] = new_id
                if isinstance(item, BaseNode): internal_nodes_data.append(item_data)
                elif isinstance(item, CommentItem): internal_comments_data.append(item_data)
                elif isinstance(item, FrameItem): internal_frames_data.append(item_data)
            if min_x == float('inf'): log.error("Cannot determine bounds."); return None, None
            for data_list in [internal_nodes_data, internal_comments_data, internal_frames_data]:
                 for item_data in data_list:
                      ox, oy = item_data['pos']; item_data['pos'] = (ox - min_x, oy - min_y)
        except Exception as e: log.error(f"Error processing items: {e}", exc_info=True); return None, None

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
                    new_from, new_to = old_id_to_new_id.get(conn_data['from_node']), old_id_to_new_id.get(conn_data['to_node'])
                    if new_from and new_to: conn_data['from_node'], conn_data['to_node'] = new_from, new_to; internal_connections_data.append(conn_data)
                elif not start_in and end_in: potential_inputs.append((end_node.pos().y(), conn.to_data(), conn))
                elif start_in and not end_in: potential_outputs.append((start_node.pos().y(), conn.to_data(), conn))
        except Exception as e: log.error(f"Error analyzing connections: {e}", exc_info=True); return None, None

        try:
            potential_inputs.sort(key=lambda x: x[2].start_socket.parentItem().pos().y())
            potential_outputs.sort(key=lambda x: x[2].end_socket.parentItem().pos().y())
            log.debug("Sorted IO by external node Y-pos.")
        except Exception as e: log.error(f"Err sort IO: {e}. Fallback.", exc_info=True); potential_inputs.sort(key=lambda x: x[0]); potential_outputs.sort(key=lambda x: x[0])

        macro_inputs, macro_outputs = [], []
        input_name_base, output_name_base = "Вхід", "Вихід"
        input_idx, output_idx = 1, 1

        for i, (_, original_conn_data, original_conn_obj) in enumerate(potential_inputs):
            input_name = f"{input_name_base} {input_idx}"; input_idx += 1
            macro_input_node = MacroInputNode(name=input_name)
            input_x = -macro_input_node.width - 50
            input_y = center_y + (i - (len(potential_inputs) - 1) / 2) * 70
            input_node_data = macro_input_node.to_data(); input_node_data['pos'] = (input_x, input_y)
            internal_nodes_data.append(input_node_data)
            log.debug(f"  Created MacroInput '{input_name}' at ({input_x:.1f}, {input_y:.1f})")
            internal_target_id = old_id_to_new_id.get(original_conn_data['to_node'])
            internal_target_sock = original_conn_data['to_socket']
            macro_inputs.append({'name': input_name, 'internal_node_id': internal_target_id, 'internal_socket_name': internal_target_sock, 'macro_input_node_id': input_node_data['id']})
            internal_connections_data.append({'from_node': input_node_data['id'], 'from_socket': 'out', 'to_node': internal_target_id, 'to_socket': internal_target_sock})
            external_connections_info.append({'type': 'input', 'original_conn_data': original_conn_data, 'target_input_name': input_name, 'original_conn': original_conn_obj})

        for i, (_, original_conn_data, original_conn_obj) in enumerate(potential_outputs):
            output_name = f"{output_name_base} {output_idx}"; output_idx += 1
            macro_output_node = MacroOutputNode(name=output_name)
            output_x = normalized_width + 50
            output_y = center_y + (i - (len(potential_outputs) - 1) / 2) * 70
            output_node_data = macro_output_node.to_data(); output_node_data['pos'] = (output_x, output_y)
            internal_nodes_data.append(output_node_data)
            log.debug(f"  Created MacroOutput '{output_name}' at ({output_x:.1f}, {output_y:.1f})")
            internal_source_id = old_id_to_new_id.get(original_conn_data['from_node'])
            internal_source_sock = original_conn_data['from_socket']
            macro_outputs.append({'name': output_name, 'internal_node_id': internal_source_id, 'internal_socket_name': internal_source_sock, 'macro_output_node_id': output_node_data['id']})
            internal_connections_data.append({'from_node': internal_source_id, 'from_socket': internal_source_sock, 'to_node': output_node_data['id'], 'to_socket': 'in'})
            external_connections_info.append({'type': 'output', 'original_conn_data': original_conn_data, 'source_output_name': output_name, 'original_conn': original_conn_obj})

        macro_data = {'id': macro_id, 'name': macro_name, 'nodes': internal_nodes_data, 'connections': internal_connections_data, 'comments': internal_comments_data, 'frames': internal_frames_data, 'inputs': macro_inputs, 'outputs': macro_outputs}
        # Визначення додається в redo() через project_manager
        log.debug(f"Macro definition '{macro_id}' created (data prepared).")
        return macro_data, external_connections_info

    def _create_and_add_macro_node(self, macro_data=None):
         if not self.macro_id or not self.project_manager: log.error("Cannot create MacroNode: Macro ID/PM missing."); self.setObsolete(True); return None
         if not macro_data: macro_data = self.project_manager.get_macro_data(self.macro_id)
         if not macro_data: log.error(f"Macro def {self.macro_id} not found."); self.setObsolete(True); return None
         center_pos = QPointF(0, 0)
         node_positions = [QPointF(*item['data']['pos']) for item in self.removed_items_data if item['type'] in ['node', 'comment', 'frame']]
         if node_positions:
             try: cx = sum(p.x() for p in node_positions) / len(node_positions); cy = sum(p.y() for p in node_positions) / len(node_positions); center_pos = QPointF(cx, cy)
             except ZeroDivisionError: log.warning("Cannot calc center pos."); center_pos = QPointF(*self.removed_items_data[0]['data'].get('pos', (0,0))) if self.removed_items_data else center_pos
         log.debug(f"Center pos for MacroNode: ({center_pos.x():.1f}, {center_pos.y():.1f})")
         try:
             macro_node = MacroNode(macro_id=self.macro_id, name=macro_data['name'])
             self.macro_node_id = macro_node.id; macro_node.setPos(center_pos)
             macro_node.update_sockets_from_definition(macro_data)
             self.scene.addItem(macro_node); self.scene.clearSelection(); macro_node.setSelected(True)
             log.debug(f"Created/added MacroNode {self.macro_node_id} at {center_pos}")
             return macro_node
         except Exception as e: log.error(f"Error create/add MacroNode: {e}", exc_info=True); self.setObsolete(True); return None


    def _reconnect_external_connections(self, macro_node, external_connections_info=None):
        if external_connections_info is None: external_connections_info = self.external_connections_data
        log.debug(f"Reconnecting {len(external_connections_info)} external conns to {macro_node.id}...")
        new_connections_refs = []
        for info in external_connections_info:
            original_data = info['original_conn_data']; new_conn = None
            try:
                if info['type'] == 'input':
                    ext_node_id, ext_sock_name = original_data['from_node'], original_data['from_socket']
                    macro_sock_name = info['target_input_name']
                    ext_node = next((i for i in self.scene.items() if isinstance(i, BaseNode) and i.id == ext_node_id), None)
                    if ext_node:
                        ext_sock, macro_sock = ext_node.get_socket(ext_sock_name), macro_node.get_socket(macro_sock_name)
                        if ext_sock and macro_sock and not any(c.end_socket == macro_sock for c in ext_sock.connections):
                            new_conn = Connection(ext_sock, macro_sock); log.debug(f"  Input: {ext_node_id}:{ext_sock_name} -> Macro:{macro_sock_name}")
                        elif not (ext_sock and macro_sock): log.warning(f"  Input fail: Sockets not found (ext:{ext_sock}, macro:{macro_sock}, name:{macro_sock_name})")
                    else: log.warning(f"  Input fail: Ext node {ext_node_id} not found.")
                elif info['type'] == 'output':
                    ext_node_id, ext_sock_name = original_data['to_node'], original_data.get('to_socket', 'in')
                    macro_sock_name = info['source_output_name']
                    ext_node = next((i for i in self.scene.items() if isinstance(i, BaseNode) and i.id == ext_node_id), None)
                    if ext_node:
                        ext_sock, macro_sock = ext_node.get_socket(ext_sock_name), macro_node.get_socket(macro_sock_name)
                        if ext_sock and macro_sock and not any(c.end_socket == ext_sock for c in macro_sock.connections):
                            new_conn = Connection(macro_sock, ext_sock); log.debug(f"  Output: Macro:{macro_sock_name} -> {ext_node_id}:{ext_sock_name}")
                        elif not (ext_sock and macro_sock): log.warning(f"  Output fail: Sockets not found (macro:{macro_sock}, ext:{ext_sock}, name:{macro_sock_name})")
                    else: log.warning(f"  Output fail: Ext node {ext_node_id} not found.")
                if new_conn:
                    self.scene.addItem(new_conn)
                    new_connections_refs.append({'start_ref': {'node_id': new_conn.start_socket.parentItem().id, 'socket_name': new_conn.start_socket.socket_name}, 'end_ref': {'node_id': new_conn.end_socket.parentItem().id, 'socket_name': new_conn.end_socket.socket_name}})
            except Exception as e: log.error(f" Err reconnecting {info}: {e}", exc_info=True)
        return new_connections_refs
