import uuid
import logging # Додано
from copy import deepcopy # Додано для копіювання структур даних
from lxml import etree as ET
from PyQt6.QtGui import QUndoCommand
from PyQt6.QtCore import QPointF, QRectF
# Додано імпорти для запиту імені макросу
from PyQt6.QtWidgets import QInputDialog, QLineEdit

# Імпортуємо DecoratorNode для перевірки в AddConnectionCommand
from nodes import (BaseNode, Connection, CommentItem, FrameItem, TriggerNode, DecoratorNode, MacroNode,
                   MacroInputNode, MacroOutputNode, NODE_REGISTRY, generate_short_id)

log = logging.getLogger(__name__) # Додано


class AddNodeCommand(QUndoCommand):
    def __init__(self, scene, node_type_name, position, parent=None):
        super().__init__(parent)
        self.scene = scene
        self.node_type_name = node_type_name
        self.position = position
        node_class = NODE_REGISTRY.get(node_type_name, BaseNode)
        self.node = node_class()
        self.node.setPos(self.position)
        self.setText(f"Додати вузол {self.node.node_type}")
        # Отримуємо main_window безпечно
        self.main_window = next((v.parent() for v in self.scene.views() if hasattr(v, 'parent') and callable(v.parent)), None)
        if self.main_window:
            self.node.update_display_properties(self.main_window.project_data.get('config'))
        else:
            log.warning("AddNodeCommand: Could not find MainWindow.")

    def redo(self):
        self.scene.addItem(self.node)
        self.scene.clearSelection()
        self.node.setSelected(True)

    def undo(self):
        if self.node.scene() == self.scene: # Перевірка, чи вузол ще на сцені
             self.scene.removeItem(self.node)


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
        self.main_window = next((v.parent() for v in self.scene.views() if hasattr(v, 'parent') and callable(v.parent)), None)


    def redo(self):
        self.scene.addItem(self.new_node)

        start_node = next(
            (item for item in self.scene.items() if isinstance(item, BaseNode) and item.id == self.start_node_id), None)
        if not start_node:
            log.error(f"AddNodeAndConnectCommand: Start node {self.start_node_id} not found.")
            self.setObsolete(True)
            # Видаляємо щойно доданий вузол, якщо початок не знайдено
            if self.new_node.scene() == self.scene:
                 self.scene.removeItem(self.new_node)
            return

        start_socket = start_node.get_socket(self.start_socket_name)
        # Перевіряємо чи є у нового вузла вхідний сокет 'in'
        end_socket = self.new_node.get_socket("in") # Використовуємо get_socket

        if not (start_socket and end_socket):
            log.error(f"AddNodeAndConnectCommand: Socket not found (start={start_socket}, end={end_socket}) for new node type {self.node_type_name}")
            self.setObsolete(True)
            if self.new_node.scene() == self.scene:
                 self.scene.removeItem(self.new_node)
            return

        # The new node should always be connected via its input socket
        is_output_from_start = start_socket.is_output # Перевіряємо напрямок

        if self.connection is None:
            if is_output_from_start and not end_socket.is_output: # Вихід -> Вхід
                self.connection = Connection(start_socket, end_socket)
            elif not is_output_from_start and end_socket.is_output: # Вхід -> Вихід (не типово для цього меню)
                self.connection = Connection(end_socket, start_socket) # Перевертаємо
            else: # Вхід до входу або вихід до виходу - невалідно
                log.error(f"AddNodeAndConnectCommand: Invalid connection direction (output->output or input->input).")
                self.setObsolete(True)
                if self.new_node.scene() == self.scene:
                    self.scene.removeItem(self.new_node)
                return
        else:
            # Re-establish connections if they were broken during undo
            # Потрібно знайти сокети заново, бо об'єкти могли бути видалені/створені
            start_socket_restored = start_node.get_socket(self.start_socket_name) if start_node else None
            end_socket_restored = self.new_node.get_socket("in") if self.new_node else None

            if start_socket_restored and end_socket_restored:
                 self.connection.start_socket = start_socket_restored
                 self.connection.end_socket = end_socket_restored
                 self.connection.start_socket.add_connection(self.connection)
                 self.connection.end_socket.add_connection(self.connection)
            else:
                 log.error("AddNodeAndConnectCommand redo: Failed to restore sockets for existing connection.")
                 self.setObsolete(True)
                 if self.new_node.scene() == self.scene: self.scene.removeItem(self.new_node)
                 # Потенційно потрібно видалити і connection, якщо він вже на сцені
                 if self.connection and self.connection.scene() == self.scene: self.scene.removeItem(self.connection)
                 return


        if self.connection.scene() != self.scene: # Додаємо тільки якщо його немає
             self.scene.addItem(self.connection)
        self.connection.update_path()

        if self.main_window:
            self.new_node.update_display_properties(self.main_window.project_data.get('config'))
        else:
            log.warning("AddNodeAndConnectCommand: Could not find MainWindow for property update.")

        self.scene.clearSelection()
        self.new_node.setSelected(True)

    def undo(self):
        if self.connection:
            start_socket = self.connection.start_socket
            end_socket = self.connection.end_socket
            if start_socket: start_socket.remove_connection(self.connection)
            if end_socket: end_socket.remove_connection(self.connection)
            if self.connection.scene() == self.scene:
                self.scene.removeItem(self.connection)

        if self.new_node.scene() == self.scene:
            self.scene.removeItem(self.new_node)


class AddCommentCommand(QUndoCommand):
    def __init__(self, scene, position, view, parent=None):
        super().__init__(parent)
        self.scene = scene
        self.position = position
        # Зберігаємо дані замість об'єкта
        self.comment_data = {'text': "Коментар", 'pos': (position.x(), position.y()), 'size': (200, 100)}
        self.comment_item = None # Створюється в redo
        self.view = view # Потрібен для створення CommentItem

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
        self.item_class = type(item) # Зберігаємо тип елемента
        self.old_dims = old_dims
        self.new_dims = new_dims
        item_type = "коментаря" if isinstance(item, CommentItem) else "фрейму" if isinstance(item,
                                                                                             FrameItem) else "елемента"
        self.setText(f"Змінити розмір {item_type}")

    def _find_item(self):
         """Знаходить елемент на сцені за ID."""
         # Додаємо перевірку на існування сцени у self.item
         if not self.item_id or not self.item or not self.item.scene(): return None
         return next((i for i in self.item.scene().items() if isinstance(i, self.item_class) and hasattr(i, 'id') and i.id == self.item_id), None)


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
            self.setObsolete(True) # Робимо команду недійсною
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
            if node: # Застосовуємо скасування тільки якщо вузол знайдено
                node.setPos(pos)


class RemoveItemsCommand(QUndoCommand):
    def __init__(self, scene, items, parent=None):
        super().__init__(parent)
        self.scene = scene
        self.items_data = [] # Stores {'type': str, 'data': dict} for restoration

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
            try: # Додаємо try-except навколо to_data
                if isinstance(item, BaseNode):
                    item_data = item.to_data()
                    item_type = 'node'
                elif isinstance(item, Connection):
                    # Ensure connection data is valid before storing
                    conn_data = item.to_data()
                    if conn_data: # Only store if nodes exist and data is valid
                        item_data = conn_data
                        item_type = 'connection'
                    else:
                        log.warning(f"RemoveItemsCommand: Skipping invalid connection during data saving (obj: {item}).")
                        connections_to_remove_explicitly.add(item) # Mark for explicit removal
                elif isinstance(item, (CommentItem, FrameItem)):
                    item_data = item.to_data()
                    item_type = 'container'
                # Add other types if needed

                if item_type and item_data: # Only store if valid type and data
                    self.items_data.append({'type': item_type, 'data': item_data})
            except Exception as e:
                 log.error(f"Error calling to_data() for item {item}: {e}", exc_info=True)
                 # Якщо to_data() падає, ми не можемо зберегти дані, але маємо видалити об'єкт
                 # Можливо, позначити його для явного видалення без збереження?
                 # Поки що пропускаємо збереження, redo все одно видалить об'єкт

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
                # else: log.debug(f" Connection {item} not on scene.")

        # Remove nodes and containers
        for item in self.items_to_remove_objects:
             if item not in connections_removed: # Перевіряємо, чи ще не видалено
                 if item.scene() == self.scene:
                     try:
                         self.scene.removeItem(item)
                     except Exception as e:
                         log.error(f"Error removing item {item} in redo: {e}", exc_info=True)
                 # else: log.debug(f" Item {item} not on scene or already removed.")

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
        nodes_map = {} # Map ID -> restored Node object
        created_items_map = {} # Map tuple(item_data) -> restored item object

        # Get the view for creating Comment/Frame items
        view = self.scene.views()[0] if self.scene.views() else None

        # Add nodes and containers back first
        for item_info in self.items_data:
            item_type = item_info['type']
            item_data = item_info['data']
            restored_item = None

            try:
                if item_type == 'node':
                    restored_item = BaseNode.from_data(item_data)
                    nodes_map[restored_item.id] = restored_item
                elif item_type == 'container':
                    # Використовуємо більш надійне визначення типу
                    item_id = item_data.get('id', '') # ID може бути порожнім, якщо щось пішло не так
                    # Спробуємо визначити за наявністю header_height (не дуже надійно) або за іменем
                    # Краще додати поле 'container_type' в to_data
                    if 'size' in item_data and isinstance(item_data['size'], (tuple, list)) and len(item_data['size']) == 2:
                        # Евристика за текстом або можна додати тип в to_data()
                        if item_data.get('text', '').startswith("Новая группа"): # Frame heuristic
                             restored_item = FrameItem.from_data(item_data, view)
                        else: # Assume Comment
                             restored_item = CommentItem.from_data(item_data, view)
                    else:
                        log.warning(f" Invalid/missing size data for container: {item_data}")


                if restored_item:
                     # Add item only if it's not already on the scene (e.g., from a partial undo)
                     existing_item = next((i for i in self.scene.items() if hasattr(i, 'id') and i.id == restored_item.id), None)
                     if not existing_item:
                          self.scene.addItem(restored_item)
                     elif existing_item != restored_item:
                          log.warning(f"Item {restored_item.id} already exists on scene, but is a different object during undo. Replacing.")
                          self.scene.removeItem(existing_item)
                          self.scene.addItem(restored_item)

                     # Use frozenset for dictionary key
                     created_items_map[frozenset(item_data.items())] = restored_item
                     # log.debug(f" Restored {item_type}: {restored_item.id if hasattr(restored_item, 'id') else restored_item}")
                # else:
                    # log.error(f" Failed to restore item from data: {item_data}") # Reduced verbosity

            except Exception as e:
                 log.error(f" Error restoring item {item_type} from data {item_data}: {e}", exc_info=True)


        # Restore connections
        for item_info in self.items_data:
            if item_info['type'] == 'connection':
                conn_data = item_info['data']
                from_node = nodes_map.get(conn_data['from_node'])
                to_node = nodes_map.get(conn_data['to_node'])

                # Try finding nodes on scene if not in restored map (maybe they weren't removed)
                if not from_node: from_node = next((item for item in self.scene.items() if isinstance(item, BaseNode) and item.id == conn_data['from_node']), None)
                if not to_node: to_node = next((item for item in self.scene.items() if isinstance(item, BaseNode) and item.id == conn_data['to_node']), None)


                if from_node and to_node:
                    start_socket = from_node.get_socket(conn_data['from_socket'])
                    to_socket_name = conn_data.get('to_socket', 'in')
                    end_socket = to_node.get_socket(to_socket_name)

                    if start_socket and end_socket:
                        # Check if this connection already exists
                        already_exists = False
                        for existing_conn in start_socket.connections:
                            if existing_conn.end_socket == end_socket:
                                already_exists = True
                                break
                        if not already_exists:
                            try:
                                conn = Connection(start_socket, end_socket)
                                self.scene.addItem(conn)
                                # Use frozenset for dictionary key
                                created_items_map[frozenset(conn_data.items())] = conn
                            except Exception as e:
                                log.error(f"Error creating/adding connection in undo: {e}", exc_info=True)

                        # else: log.debug(f" Connection already exists: {from_node.id} -> {to_node.id}")

                    # else: log.warning(f"RemoveItemsCommand undo: Could not find sockets for connection {conn_data}")
                # else: log.warning(f"RemoveItemsCommand undo: Could not find nodes for connection {conn_data}")


class MoveItemsCommand(QUndoCommand):
    def __init__(self, items_map, parent=None):
        super().__init__(parent)
        self.scene = list(items_map.keys())[0].scene() if items_map else None
        # Зберігаємо ID та старі/нові позиції
        self.items_pos_data = []
        for item, (old_pos, new_pos) in items_map.items():
             if hasattr(item, 'id'): # Зберігаємо тільки для елементів з ID
                  self.items_pos_data.append({'id': item.id, 'class': type(item), 'old_pos': old_pos, 'new_pos': new_pos})
        self.setText("Перемістити елементи")

    def _find_item(self, item_id, item_class):
         """Знаходить елемент на сцені за ID та класом."""
         if not self.scene: return None
         return next((i for i in self.scene.items() if isinstance(i, item_class) and hasattr(i, 'id') and i.id == item_id), None)


    def _apply_pos(self, pos_key):
        if not self.scene: return
        for data in self.items_pos_data:
            item = self._find_item(data['id'], data['class'])
            if item: # Застосовуємо позицію тільки якщо елемент знайдено
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
            # Відміняємо створення команди
            # Це не стандартний спосіб, але спробуємо так
            # Краще б перевіряти це перед створенням команди в EditorView
            self.start_socket_ref = None
            self.end_socket_ref = None
        else:
            self.start_socket_ref = {'node_id': start_node.id, 'socket_name': start_socket.socket_name}
            self.end_socket_ref = {'node_id': end_node.id, 'socket_name': end_socket.socket_name}

        self.connection_id = None # ID буде присвоєно самому Connection при створенні, тут не зберігаємо
        self.connection = None # Connection object created in redo
        self.setText("Додати з'єднання")
        log.debug(f"AddConnectionCommand initialized: {self.start_socket_ref} -> {self.end_socket_ref}")


    def _find_socket(self, ref):
        """Helper to find socket by reference, returns None if not found."""
        if not self.scene or not ref: return None # Додано перевірку ref
        node = next((item for item in self.scene.items() if isinstance(item, BaseNode) and item.id == ref['node_id']), None)
        if node:
            socket = node.get_socket(ref['socket_name'])
            # if not socket: log.warning(f"AddConnectionCommand: Socket '{ref['socket_name']}' not found on node {ref['node_id']}.")
            return socket
        # log.warning(f"AddConnectionCommand: Node {ref['node_id']} not found during socket lookup.")
        return None

    def redo(self):
        # Перевірка, чи команда валідна
        if not self.start_socket_ref or not self.end_socket_ref:
            log.error("AddConnectionCommand redo: Command initialized with invalid socket refs.")
            self.setObsolete(True)
            return

        log.debug("AddConnectionCommand redo: Finding sockets...")
        start_socket = self._find_socket(self.start_socket_ref)
        end_socket = self._find_socket(self.end_socket_ref)

        log.debug(f"  Start socket found: {start_socket is not None} (Node: {self.start_socket_ref['node_id']}, Socket: {self.start_socket_ref['socket_name']})")
        log.debug(f"  End socket found: {end_socket is not None} (Node: {self.end_socket_ref['node_id']}, Socket: {self.end_socket_ref['socket_name']})")


        if not (start_socket and end_socket):
            log.error("AddConnectionCommand redo: Could not find sockets. Command obsolete.")
            self.setObsolete(True)
            return

        try: # Обгортаємо всю логіку redo в try-except
            log.debug("AddConnectionCommand redo: Validating connection...")
            # Валідація: Чи можна з'єднувати ці сокети?
            if start_socket.is_output == end_socket.is_output:
                 log.error("AddConnectionCommand redo: Invalid connection direction (output->output or input->input). Command obsolete.")
                 self.setObsolete(True)
                 return
            # Гарантуємо, що start_socket - це вихід (важливо для подальших перевірок)
            if not start_socket.is_output:
                log.debug("  Swapping start/end sockets as start was input.")
                start_socket, end_socket = end_socket, start_socket
                # Оновлюємо refs, якщо ми їх перевернули
                self.start_socket_ref, self.end_socket_ref = self.end_socket_ref, self.start_socket_ref


            # Перевірка: чи вхідний сокет вже зайнятий?
            log.debug(f"  Checking input socket {end_socket.socket_name} on {end_socket.parent_node.id}. Connections: {len(end_socket.connections)}")
            if len(end_socket.connections) > 0:
                 # Перевіряємо, чи це не те саме з'єднання, яке ми намагаємося відновити
                 is_self_connection = self.connection and self.connection in end_socket.connections
                 if not is_self_connection:
                      log.error("AddConnectionCommand redo: Input socket already has a connection. Command obsolete.")
                      self.setObsolete(True)
                      return
                 else:
                      log.debug("  Input socket check passed (restoring self).")


            # Перевірка: чи вихідний сокет на Trigger/Decorator вже зайнятий (якщо це 'out', 'out_loop', 'out_end')?
            output_node = start_socket.parentItem()
            log.debug(f"  Checking output socket {start_socket.socket_name} on {output_node.id} ({type(output_node).__name__}). Connections: {len(start_socket.connections)}")
            if isinstance(output_node, (TriggerNode, DecoratorNode)):
                 # Перевіряємо тільки сокети, що можуть мати лише одне з'єднання
                 restricted_sockets = ('out', 'out_loop', 'out_end') # У TriggerNode є 'out', у Decorator - 'out_loop'/'out_end'
                 if start_socket.socket_name in restricted_sockets and len(start_socket.connections) > 0:
                      is_self_connection = self.connection and self.connection in start_socket.connections
                      if not is_self_connection:
                           log.error(f"AddConnectionCommand redo: Output socket {start_socket.socket_name} on {type(output_node).__name__} already has a connection. Command obsolete.")
                           self.setObsolete(True)
                           return
                      else:
                           log.debug("  Output socket check passed (restoring self).")


            log.debug("AddConnectionCommand redo: Creating/Restoring connection object...")
            if self.connection is None:
                log.debug(f"  Creating new Connection: {start_socket.socket_name} ({start_socket.parent_node.id}) -> {end_socket.socket_name} ({end_socket.parent_node.id})")
                self.connection = Connection(start_socket, end_socket)
            else:
                log.debug(f"  Restoring existing Connection: {start_socket.socket_name} ({start_socket.parent_node.id}) -> {end_socket.socket_name} ({end_socket.parent_node.id})")
                # Re-establish potentially broken links after undo/redo cycles
                self.connection.start_socket = start_socket
                self.connection.end_socket = end_socket
                # Додаємо з'єднання до сокетів тільки якщо їх там ще немає
                if self.connection not in start_socket.connections:
                     log.debug(f"  Adding connection back to start socket {start_socket.socket_name}")
                     start_socket.add_connection(self.connection)
                if self.connection not in end_socket.connections:
                     log.debug(f"  Adding connection back to end socket {end_socket.socket_name}")
                     end_socket.add_connection(self.connection)


            log.debug("AddConnectionCommand redo: Adding connection to scene...")
            if self.connection.scene() != self.scene: # Add only if not already on scene
                self.scene.addItem(self.connection)

            log.debug("AddConnectionCommand redo: Updating connection path...")
            self.connection.update_path()
            self.scene.clearSelection()
            # Не виділяємо з'єднання після створення, це може бути незручно
            # self.connection.setSelected(True)
            log.debug("AddConnectionCommand redo: Finished successfully.")

        except Exception as e:
             log.error(f"AddConnectionCommand redo: Unexpected error: {e}", exc_info=True)
             self.setObsolete(True)
             # Спробуємо безпечно видалити щойно створене з'єднання, якщо воно додалося
             if self.connection and self.connection.scene() == self.scene:
                  log.debug("  Attempting to remove partially added connection due to error.")
                  if self.connection.start_socket: self.connection.start_socket.remove_connection(self.connection)
                  if self.connection.end_socket: self.connection.end_socket.remove_connection(self.connection)
                  self.scene.removeItem(self.connection)


    def undo(self):
        log.debug("AddConnectionCommand undo: Starting...")
        # Знаходимо сокети, використовуючи посилання, що могли бути перевернуті в redo
        start_socket = self._find_socket(self.start_socket_ref)
        end_socket = self._find_socket(self.end_socket_ref)
        connection_to_remove = None

        log.debug(f"  Undo: Start socket found: {start_socket is not None}")
        log.debug(f"  Undo: End socket found: {end_socket is not None}")


        # Знаходимо об'єкт Connection на сцені АБО через збережене посилання self.connection
        if self.connection:
             # Перевіряємо, чи збігаються сокети збереженого з'єднання з поточними
             if self.connection.start_socket == start_socket and self.connection.end_socket == end_socket:
                  connection_to_remove = self.connection
                  log.debug("  Undo: Found connection using self.connection reference.")
             else:
                  log.warning("  Undo: self.connection sockets do not match found sockets. Searching on scene...")
                  # Спробуємо знайти на сцені, якщо посилання застаріли
                  if start_socket:
                       connection_to_remove = next((conn for conn in start_socket.connections if conn.end_socket == end_socket), None)


        # Якщо не знайшли через self.connection, шукаємо на сцені
        if not connection_to_remove and start_socket:
             log.debug("  Undo: Searching for connection via start_socket...")
             connection_to_remove = next((conn for conn in start_socket.connections if conn.end_socket == end_socket), None)
        if not connection_to_remove and end_socket: # Якщо напрямок був інший (хоча redo мав це виправити)
             log.debug("  Undo: Searching for connection via end_socket (reverse)...")
             connection_to_remove = next((conn for conn in end_socket.connections if conn.start_socket == start_socket), None)


        # Видаляємо з'єднання
        if connection_to_remove:
            log.debug(f"  Undo: Found connection to remove: {connection_to_remove}")
            start_sock = connection_to_remove.start_socket
            end_sock = connection_to_remove.end_socket
            # Спочатку видаляємо посилання з сокетів
            log.debug(f"  Undo: Removing connection from sockets {start_sock.socket_name if start_sock else '?'} and {end_sock.socket_name if end_sock else '?'}")
            if start_sock: start_sock.remove_connection(connection_to_remove)
            if end_sock: end_sock.remove_connection(connection_to_remove)
            # Потім видаляємо зі сцени
            if connection_to_remove.scene() == self.scene:
                log.debug("  Undo: Removing connection from scene.")
                self.scene.removeItem(connection_to_remove)
            # self.connection = None # Не скидаємо self.connection, він потрібен для redo
        else:
             # Якщо з'єднання не знайдено (можливо, вже видалено іншою командою)
             log.warning("AddConnectionCommand undo: Connection object not found on scene or sockets invalid.")

        log.debug("AddConnectionCommand undo: Finished.")


class ChangePropertiesCommand(QUndoCommand):
    def __init__(self, node, old_data, new_data, parent=None):
        super().__init__(parent)
        # self.node = node # Не зберігаємо пряме посилання
        # Store node ID in case the object gets deleted/recreated
        self.node_id = node.id
        self.scene = node.scene() # Зберігаємо сцену
        self.old_data = deepcopy(old_data) # Глибоке копіювання
        self.new_data = deepcopy(new_data) # Глибоке копіювання
        self.setText("Змінити властивості")

    def _find_node(self):
         """Finds the node on the scene by ID."""
         if not self.scene: return None
         return next((item for item in self.scene.items() if isinstance(item, BaseNode) and item.id == self.node_id), None)

    def _apply_data(self, data):
        node = self._find_node()
        if not node:
             log.error(f"ChangePropertiesCommand: Node {self.node_id} not found on scene.")
             self.setObsolete(True)
             return

        # Застосовуємо дані
        node.node_name = data['name']
        node.description = data['desc']
        # Переконуємось, що властивості копіюються, а не присвоюються за посиланням
        node.properties = list(data['props']) # Створюємо новий список
        # Apply macro_id if present
        if 'macro_id' in data and hasattr(node, 'macro_id'):
             node.macro_id = data['macro_id']

        # Update display and potentially UI
        main_window = next((v.parent() for v in node.scene().views() if hasattr(v, 'parent') and callable(v.parent)), None)
        if main_window:
            config = main_window.project_data.get('config')
            node.update_display_properties(config)
            # Update properties panel if this node is selected
            if main_window.current_selected_node == node:
                # Потрібно оновити UI панелі властивостей
                main_window._update_properties_panel_ui() # Викликаємо приватний метод оновлення
        else:
            log.warning("ChangePropertiesCommand: Could not find MainWindow.")

    def redo(self): self._apply_data(self.new_data)

    def undo(self): self._apply_data(self.old_data)


class PasteCommand(QUndoCommand):
    def __init__(self, scene, clipboard_text, position, parent=None):
        super().__init__(parent)
        self.scene = scene
        self.clipboard_text = clipboard_text
        self.position = position
        self.pasted_items_data = [] # Store item DATA, not objects
        self.pasted_connections_data = [] # Store connection DATA separately
        # Змінено: Зберігаємо ID створених елементів
        self.created_item_ids = {} # Map old ID -> new ID
        self.created_connection_refs = [] # List of {'start_ref':..., 'end_ref':...}
        self.setText("Вставити елементи")
        self.main_window = next((v.parent() for v in self.scene.views() if hasattr(v, 'parent') and callable(v.parent)), None)


    def redo(self):
        created_nodes = {} # Map new ID -> new Node object (тільки для цього виклику redo)

        # Якщо дані ще не підготовлені, парсимо буфер обміну
        if not self.pasted_items_data and not self.pasted_connections_data:
            # First time: parse clipboard and prepare data
            try:
                clipboard_root = ET.fromstring(self.clipboard_text.encode('utf-8'))
                nodes_xml = clipboard_root.find("nodes")
                if nodes_xml is None:
                     log.warning("PasteCommand: No <nodes> found in clipboard.")
                     self.setObsolete(True); return

                node_elements = list(nodes_xml)
                if not node_elements:
                     log.warning("PasteCommand: <nodes> section is empty.")
                     self.setObsolete(True); return

                # Calculate offset
                min_x = min((float(el.get("x", 0)) for el in node_elements))
                min_y = min((float(el.get("y", 0)) for el in node_elements))
                ref_pos = QPointF(min_x, min_y)

                # Prepare node data
                self.created_item_ids = {} # Очищаємо мапінг ID перед заповненням
                for node_el in node_elements:
                    node_data = BaseNode.data_from_xml(node_el)
                    old_id = node_data['id']
                    new_id = generate_short_id() # Generate new ID for pasted node
                    node_data['id'] = new_id # Update data with new ID
                    original_pos = QPointF(*node_data['pos'])
                    # Змінено: використовуємо .x() та .y() для QPointF
                    node_data['pos'] = ( (self.position + (original_pos - ref_pos)).x(), (self.position + (original_pos - ref_pos)).y() )
                    self.pasted_items_data.append({'type': 'node', 'data': node_data, 'old_id': old_id})
                    self.created_item_ids[old_id] = new_id # Зберігаємо мапінг старих на нові ID

                # Prepare connection data
                connections_xml = clipboard_root.find("connections")
                if connections_xml is not None:
                    for conn_el in connections_xml:
                        conn_data = Connection.data_from_xml(conn_el)
                        # Зберігаємо дані з'єднання з посиланнями на СТАРІ ID вузлів
                        self.pasted_connections_data.append(conn_data)

            except Exception as e:
                log.error(f"PasteCommand: Error parsing clipboard data: {e}", exc_info=True)
                self.setObsolete(True)
                return

        # --- Створення та додавання елементів на сцену ---

        # Створення вузлів
        config = self.main_window.project_data.get('config') if self.main_window else None
        for item_info in self.pasted_items_data:
             if item_info['type'] == 'node':
                  node_data = item_info['data']
                  new_id = node_data['id'] # Беремо новий ID з даних
                  # Перевіряємо, чи вузол з таким ID вже існує (після undo/redo)
                  existing_node = next((item for item in self.scene.items() if isinstance(item, BaseNode) and item.id == new_id), None)
                  if existing_node:
                      created_nodes[new_id] = existing_node # Використовуємо існуючий
                      if existing_node.scene() != self.scene: self.scene.addItem(existing_node) # Додаємо, якщо його видалили
                      log.debug(f"PasteCommand redo: Using existing node {new_id}")
                      continue # Переходимо до наступного

                  # Якщо вузла немає, створюємо новий
                  try:
                       new_node = BaseNode.from_data(node_data)
                       self.scene.addItem(new_node)
                       new_node.update_display_properties(config)
                       created_nodes[new_id] = new_node # Зберігаємо створений вузол за новим ID
                  except Exception as e:
                       log.error(f"PasteCommand: Failed to create node from data: {node_data}. Error: {e}", exc_info=True)

        # Створення з'єднань
        self.created_connection_refs = [] # Очищуємо для redo
        for conn_data in self.pasted_connections_data:
             new_from_id = self.created_item_ids.get(conn_data['from_node'])
             new_to_id = self.created_item_ids.get(conn_data['to_node'])

             if new_from_id and new_to_id:
                  from_node = created_nodes.get(new_from_id)
                  to_node = created_nodes.get(new_to_id)

                  if from_node and to_node:
                       from_socket = from_node.get_socket(conn_data['from_socket'])
                       to_socket_name = conn_data.get('to_socket', 'in') # TODO: Save 'to_socket' in XML?
                       to_socket = to_node.get_socket(to_socket_name)

                       if from_socket and to_socket:
                           # Перевіряємо, чи таке з'єднання вже існує
                           already_exists = False
                           for existing_conn in from_socket.connections:
                                if existing_conn.end_socket == to_socket:
                                     already_exists = True
                                     # Зберігаємо посилання для undo, навіть якщо воно вже існує
                                     self.created_connection_refs.append({
                                         'start_ref': {'node_id': new_from_id, 'socket_name': from_socket.socket_name},
                                         'end_ref': {'node_id': new_to_id, 'socket_name': to_socket.socket_name}
                                     })
                                     log.debug(f"PasteCommand redo: Connection already exists between {new_from_id} and {new_to_id}")
                                     break
                           if not already_exists:
                                conn = Connection(from_socket, to_socket)
                                self.scene.addItem(conn)
                                # Зберігаємо посилання на сокети для undo
                                self.created_connection_refs.append({
                                    'start_ref': {'node_id': new_from_id, 'socket_name': from_socket.socket_name},
                                    'end_ref': {'node_id': new_to_id, 'socket_name': to_socket.socket_name}
                                })
                       else:
                            log.warning(f"PasteCommand redo: Could not find sockets for connection: {conn_data}")
                  else:
                       log.warning(f"PasteCommand redo: Could not find created nodes for connection: {conn_data} (IDs: {new_from_id}, {new_to_id})")
             else:
                  log.warning(f"PasteCommand redo: Could not map old node IDs for connection: {conn_data}")


        # Виділення новостворених вузлів
        self.scene.clearSelection()
        for node_id, node in created_nodes.items():
             node.setSelected(True)


    def undo(self):
         created_nodes = {} # Map new ID -> Node object found on scene
         # Знаходимо вузли, що були створені, на сцені
         for old_id, new_id in self.created_item_ids.items():
              node = next((item for item in self.scene.items() if isinstance(item, BaseNode) and item.id == new_id), None)
              if node:
                   created_nodes[new_id] = node

         # Видалення створених з'єднань
         for conn_ref in self.created_connection_refs:
              start_node = created_nodes.get(conn_ref['start_ref']['node_id'])
              end_node = created_nodes.get(conn_ref['end_ref']['node_id'])
              start_socket = start_node.get_socket(conn_ref['start_ref']['socket_name']) if start_node else None
              end_socket = end_node.get_socket(conn_ref['end_ref']['socket_name']) if end_node else None

              connection_to_remove = None
              if start_socket:
                   for conn in start_socket.connections:
                        if conn.end_socket == end_socket:
                             connection_to_remove = conn
                             break
              # Видаляємо знайдене з'єднання
              if connection_to_remove:
                   if connection_to_remove.start_socket: connection_to_remove.start_socket.remove_connection(connection_to_remove)
                   if connection_to_remove.end_socket: connection_to_remove.end_socket.remove_connection(connection_to_remove)
                   if connection_to_remove.scene() == self.scene:
                        self.scene.removeItem(connection_to_remove)
              else:
                   log.warning(f"PasteCommand undo: Connection not found for refs: {conn_ref}")

         # Видалення створених вузлів
         for node_id, node in created_nodes.items():
              if node.scene() == self.scene:
                   self.scene.removeItem(node)

         # Не очищуємо created_item_ids тут, вони потрібні для redo
         # self.created_item_ids.clear()
         # self.created_connection_refs = [] # Очищуємо refs, вони генеруються в redo


class AddFrameCommand(QUndoCommand):
    def __init__(self, scene, items, parent=None):
        super().__init__(parent)
        self.scene = scene
        # Store item IDs instead of objects
        self.item_ids = [item.id for item in items if hasattr(item, 'id')]
        self.frame_data = None # Store frame data for redo
        # Змінено: не зберігаємо пряме посилання на frame
        self.frame_id = None # ID створюється в redo
        self.setText("Сгрупувати в фрейм")

    def _find_items(self):
        """Finds items on the scene by their stored IDs."""
        items = []
        if not self.scene: return items
        id_set = set(self.item_ids)
        for item in self.scene.items():
            if hasattr(item, 'id') and item.id in id_set:
                items.append(item)
        return items

    def _find_frame(self):
         """Знаходить фрейм на сцені за ID."""
         if not self.frame_id or not self.scene: return None
         return next((i for i in self.scene.items() if isinstance(i, FrameItem) and i.id == self.frame_id), None)

    def redo(self):
        frame = self._find_frame()
        if frame: # Якщо фрейм вже існує (повторне redo)
             if frame.scene() != self.scene: # Додаємо, якщо його видалили
                  self.scene.addItem(frame)
             self.scene.clearSelection()
             frame.setSelected(True)
             return

        # Перше виконання redo або після undo
        items = self._find_items()
        if not items:
            log.warning("AddFrameCommand redo: No valid items found for framing.")
            self.setObsolete(True)
            return

        # Розрахунок меж і створення даних фрейму
        bounding_rect = QRectF()
        for item in items:
            bounding_rect = bounding_rect.united(item.sceneBoundingRect())

        padding = 20
        frame_rect = bounding_rect.adjusted(-padding, -padding, padding, padding)
        frame_pos = frame_rect.topLeft()
        frame_size = (frame_rect.width(), frame_rect.height())

        # Create frame data
        self.frame_id = generate_short_id()
        self.frame_data = {
            'id': self.frame_id,
            'text': "Новая группа",
            'pos': (frame_pos.x(), frame_pos.y()),
            'size': frame_size
        }
        # Create frame object from data
        view = self.scene.views()[0] if self.scene.views() else None
        frame = FrameItem.from_data(self.frame_data, view)

        # Add frame to scene
        self.scene.addItem(frame)
        self.scene.clearSelection()
        frame.setSelected(True)

    def undo(self):
        # Remove frame from scene
        frame = self._find_frame()
        if frame and frame.scene() == self.scene:
            self.scene.removeItem(frame)

        # Reselect original items
        items = self._find_items()
        self.scene.clearSelection()
        for item in items:
            item.setSelected(True)


class UngroupFrameCommand(QUndoCommand):
    def __init__(self, scene, frame, parent=None):
        super().__init__(parent)
        self.scene = scene
        self.frame_data = frame.to_data() # Store frame data for undo
        self.frame_id = frame.id # Зберігаємо ID
        self.setText("Разгрупувати фрейм")

    def _find_frame(self):
         """Знаходить фрейм на сцені за ID."""
         if not self.frame_id or not self.scene: return None
         return next((i for i in self.scene.items() if isinstance(i, FrameItem) and i.id == self.frame_id), None)

    def redo(self):
        # Remove frame from scene
        frame = self._find_frame()
        if frame and frame.scene() == self.scene:
            self.scene.removeItem(frame)
        self.scene.clearSelection()
        # Items inside remain selected or not based on previous state

    def undo(self):
        # Recreate frame from data if it's not on the scene
        frame = self._find_frame()
        if not frame:
             view = self.scene.views()[0] if self.scene.views() else None
             frame = FrameItem.from_data(self.frame_data, view)
             self.scene.addItem(frame)
        elif frame.scene() != self.scene: # Якщо існує, але не на сцені
             self.scene.addItem(frame)

        self.scene.clearSelection()
        if frame: frame.setSelected(True)


# --- Команда для створення макросу (оновлена) ---
class CreateMacroCommand(QUndoCommand):
    def __init__(self, main_window, selected_items, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.scene = main_window.scene
        # Зберігаємо дані виділених елементів для відновлення
        self.removed_items_data = [] # {'type': 'node'/'connection'/'container', 'data': ...}
        self.external_connections_data = [] # Дані зовнішніх з'єднань, які були перепідключені

        # ID нових елементів, створених в redo
        self.macro_id = None
        self.macro_node_id = None
        self.new_external_connections_refs = [] # Посилання на сокети нових з'єднань

        # Зберігаємо ID виділених для початкового пошуку
        self.initial_selected_ids = {item.id for item in selected_items if hasattr(item, 'id')}

        self.setText("Створити Макрос")
        log.debug(f"CreateMacroCommand initialized with {len(self.initial_selected_ids)} selected item IDs.")


    def redo(self):
        log.debug("CreateMacroCommand redo executing...")
        macro_node = None # Визначаємо змінну тут

        # Якщо макрос вже створено (повторне redo), відновлюємо стан
        if self.macro_id:
            log.debug(f"Redoing macro creation for {self.macro_id}...")
            # 1. Додаємо визначення макросу назад (якщо його видалили)
            # Потрібні дані макросу, збережені в self.macro_data_backup
            if hasattr(self, 'macro_data_backup') and self.macro_id not in self.main_window.project_data.get('macros', {}):
                 self.main_window.project_data.setdefault('macros', {})[self.macro_id] = self.macro_data_backup
                 log.debug(f"Restored macro definition {self.macro_id}")
            elif not hasattr(self, 'macro_data_backup'):
                 log.warning("Cannot redo macro definition restoration - backup data missing.")

            # 2. Видаляємо відновлені елементи з undo (якщо вони є)
            self._remove_restored_items()

            # 3. Створюємо та додаємо MacroNode (якщо його немає)
            macro_node = self._find_macro_node()
            if not macro_node:
                 # Потрібні дані макросу для update_sockets_from_definition
                 macro_data = self.main_window.project_data.get('macros', {}).get(self.macro_id)
                 if macro_data:
                      macro_node = self._create_and_add_macro_node(macro_data) # Передаємо macro_data
                 else:
                      log.error(f"Cannot recreate MacroNode: definition {self.macro_id} not found.")
                      self.setObsolete(True)
                      return

            # 4. Перепідключаємо зовнішні з'єднання до MacroNode
            if macro_node: # Перевіряємо, чи вузол успішно створено/знайдено
                self.new_external_connections_refs = self._reconnect_external_connections(macro_node, self.external_connections_data)
            log.debug("Macro creation redo finished.")
            return

        # Перше виконання redo
        log.debug("First execution: Creating macro...")

        # Знаходимо актуальні об'єкти виділених елементів за ID
        selected_items = {item for item in self.scene.items() if hasattr(item, 'id') and item.id in self.initial_selected_ids}
        if not selected_items:
             log.warning("CreateMacroCommand redo: Initial selected items not found.")
             self.setObsolete(True)
             return

        # Створюємо визначення макросу
        try:
            macro_data, external_connections_info = self._create_macro_definition_and_analyze(selected_items)
            if not macro_data:
                self.setObsolete(True)
                return
            self.macro_id = macro_data['id']
            # Зберігаємо копію даних макросу для можливого відновлення в redo
            self.macro_data_backup = deepcopy(macro_data)
            self.external_connections_data = external_connections_info # Зберігаємо дані для undo
        except Exception as e:
            log.error(f"Error during macro definition: {e}", exc_info=True)
            self.setObsolete(True)
            return

        # Зберігаємо дані елементів, які будемо видаляти
        self.removed_items_data = []
        items_to_remove = set()
        # Вузли всередині макросу
        items_to_remove.update({item for item in selected_items if isinstance(item, BaseNode)})
        # З'єднання всередині макросу
        internal_connections = self._find_internal_connections(selected_items)
        items_to_remove.update(internal_connections)
        # Зовнішні з'єднання (які будуть перепідключені)
        items_to_remove.update({info['original_conn'] for info in external_connections_info})

        for item in items_to_remove:
            item_type = 'node' if isinstance(item, BaseNode) else 'connection' if isinstance(item, Connection) else 'container'
            try:
                item_data = item.to_data()
                if item_data: # Зберігаємо тільки валідні дані
                    self.removed_items_data.append({'type': item_type, 'data': item_data})
            except Exception as e:
                 log.error(f"Error getting data for item {item} to be removed: {e}", exc_info=True)

        # Видаляємо вихідні елементи
        self._remove_items_by_objects(items_to_remove)

        # Створюємо MacroNode
        macro_node = self._create_and_add_macro_node(macro_data)
        if not macro_node: return # Помилка вже залогована всередині

        # Перепідключаємо зовнішні з'єднання
        self.new_external_connections_refs = self._reconnect_external_connections(macro_node, external_connections_info)

        log.debug(f"Macro {self.macro_id} created successfully.")


    def undo(self):
        log.debug(f"CreateMacroCommand undo executing for macro {self.macro_id}...")
        # 1. Видаляємо MacroNode
        macro_node = self._find_macro_node()
        if macro_node and macro_node.scene() == self.scene:
            # Видаляємо нові зовнішні з'єднання, підключені до MacroNode
            for conn_ref in self.new_external_connections_refs:
                 conn = self._find_connection_by_refs(conn_ref['start_ref'], conn_ref['end_ref'])
                 if conn and conn.scene() == self.scene:
                      if conn.start_socket: conn.start_socket.remove_connection(conn)
                      if conn.end_socket: conn.end_socket.remove_connection(conn)
                      self.scene.removeItem(conn)
            self.new_external_connections_refs = [] # Очищуємо список посилань
            # Видаляємо сам MacroNode
            self.scene.removeItem(macro_node)
            log.debug(f"Removed MacroNode {self.macro_node_id}")

        # 2. Відновлюємо видалені елементи (вузли, внутрішні та старі зовнішні з'єднання)
        self._restore_removed_items()

        # 3. Видаляємо визначення макросу з project_data
        if self.macro_id and self.macro_id in self.main_window.project_data.get('macros', {}):
            try:
                # Зберігаємо копію перед видаленням для redo (якщо вона ще не збережена)
                if not hasattr(self, 'macro_data_backup'):
                     self.macro_data_backup = deepcopy(self.main_window.project_data['macros'][self.macro_id])
                del self.main_window.project_data['macros'][self.macro_id]
                log.debug(f"Removed macro definition {self.macro_id}")
            except KeyError:
                 log.warning(f"Could not remove macro definition {self.macro_id} - already removed?")
        # else:
             # log.warning(f"Macro definition {self.macro_id} not found for removal during undo.")
             # Не видаляємо self.macro_id тут, він потрібен для redo

        # Не скидаємо self.macro_node_id тут
        # self.macro_id = None # Не скидаємо ID, бо макрос скасовано
        # self.macro_node_id = None


    # --- Допоміжні методи ---

    def _find_macro_node(self):
         """Знаходить MacroNode на сцені за ID."""
         if not self.macro_node_id or not self.scene: return None
         return next((item for item in self.scene.items() if isinstance(item, MacroNode) and item.id == self.macro_node_id), None)

    def _find_connection_by_refs(self, start_ref, end_ref):
         """Знаходить Connection за посиланнями на сокети."""
         start_node = next((item for item in self.scene.items() if isinstance(item, BaseNode) and item.id == start_ref['node_id']), None)
         end_node = next((item for item in self.scene.items() if isinstance(item, BaseNode) and item.id == end_ref['node_id']), None)
         if start_node and end_node:
              start_socket = start_node.get_socket(start_ref['socket_name'])
              end_socket = end_node.get_socket(end_ref['socket_name'])
              if start_socket:
                   for conn in start_socket.connections:
                        if conn.end_socket == end_socket:
                             return conn
         return None


    def _get_items_by_ids(self, ids):
        """Знаходить елементи на сцені за набором ID."""
        if not self.scene: return set()
        id_set = set(ids)
        return {item for item in self.scene.items() if hasattr(item, 'id') and item.id in id_set}

    def _find_internal_connections(self, node_set):
        """Знаходить з'єднання, обидва кінці яких належать до node_set."""
        internal_connections = set()
        node_ids = {node.id for node in node_set}
        all_connections = [item for item in self.scene.items() if isinstance(item, Connection)]
        for conn in all_connections:
            start_node = conn.start_socket.parentItem() if conn.start_socket else None
            end_node = conn.end_socket.parentItem() if conn.end_socket else None
            if start_node and end_node and start_node.id in node_ids and end_node.id in node_ids:
                internal_connections.add(conn)
        return internal_connections

    def _remove_items_by_objects(self, items_to_remove):
         """Видаляє задані об'єкти зі сцени."""
         log.debug(f"Removing {len(items_to_remove)} items...")
         connections_first = {item for item in items_to_remove if isinstance(item, Connection)}
         others = items_to_remove - connections_first
         # Спочатку видаляємо з'єднання
         for conn in connections_first:
              if conn.scene() == self.scene:
                   if conn.start_socket: conn.start_socket.remove_connection(conn)
                   if conn.end_socket: conn.end_socket.remove_connection(conn)
                   self.scene.removeItem(conn)
         # Потім видаляємо решту
         for item in others:
              if item.scene() == self.scene:
                   self.scene.removeItem(item)

    def _remove_restored_items(self):
         """Видаляє елементи, що були відновлені під час undo."""
         log.debug(f"Removing {len(self.removed_items_data)} restored items...")
         items_to_remove_now = set()
         # Збираємо ID відновлених елементів
         restored_ids = {item_data['data'].get('id') for item_data in self.removed_items_data if item_data['data'].get('id')}

         # Знаходимо об'єкти на сцені за ID
         for item in self.scene.items():
              if hasattr(item, 'id') and item.id in restored_ids:
                   items_to_remove_now.add(item)
         self._remove_items_by_objects(items_to_remove_now)


    def _restore_removed_items(self):
         """Відновлює елементи зі збережених даних."""
         log.debug(f"Restoring {len(self.removed_items_data)} items...")
         restored_nodes = {} # Map ID -> Node object
         view = self.scene.views()[0] if self.scene.views() else None

         # 1. Відновлюємо вузли та контейнери
         for item_info in self.removed_items_data:
             item_type = item_info['type']
             item_data = item_info['data']
             item_id = item_data.get('id')
             restored_item = None

             # Перевіряємо, чи елемент вже існує на сцені
             existing_item = next((i for i in self.scene.items() if hasattr(i, 'id') and i.id == item_id), None) if item_id else None
             if existing_item:
                  restored_item = existing_item
                  log.debug(f"Item {item_id} already exists on scene during restore.")
                  if item_type == 'node':
                       restored_nodes[item_id] = restored_item # Додаємо існуючий до мапи
                  continue # Не створюємо заново

             # Створюємо, якщо не існує
             try:
                 if item_type == 'node':
                     restored_item = BaseNode.from_data(item_data)
                     restored_nodes[item_id] = restored_item
                 elif item_type == 'container':
                      if 'size' in item_data and isinstance(item_data['size'], (tuple, list)) and len(item_data['size']) == 2:
                          if item_data.get('text', '').startswith("Новая группа"):
                               restored_item = FrameItem.from_data(item_data, view)
                          else:
                               restored_item = CommentItem.from_data(item_data, view)
                      else: log.warning(f" Invalid size for container data: {item_data}")

                 if restored_item:
                      self.scene.addItem(restored_item)

             except Exception as e:
                  log.error(f" Error restoring item {item_type} from data {item_data}: {e}", exc_info=True)

         # 2. Відновлюємо з'єднання
         for item_info in self.removed_items_data:
             if item_info['type'] == 'connection':
                 conn_data = item_info['data']
                 from_node = restored_nodes.get(conn_data['from_node'])
                 to_node = restored_nodes.get(conn_data['to_node'])
                 # Шукаємо на сцені, якщо не знайшли у відновлених
                 if not from_node: from_node = next((item for item in self.scene.items() if isinstance(item, BaseNode) and item.id == conn_data['from_node']), None)
                 if not to_node: to_node = next((item for item in self.scene.items() if isinstance(item, BaseNode) and item.id == conn_data['to_node']), None)

                 if from_node and to_node:
                     start_socket = from_node.get_socket(conn_data['from_socket'])
                     to_socket_name = conn_data.get('to_socket', 'in')
                     end_socket = to_node.get_socket(to_socket_name)
                     if start_socket and end_socket:
                         # Перевіряємо, чи з'єднання вже існує
                         already_exists = any(conn.end_socket == end_socket for conn in start_socket.connections)
                         if not already_exists:
                             try:
                                 conn = Connection(start_socket, end_socket)
                                 self.scene.addItem(conn)
                                 conn.update_path()
                             except Exception as e:
                                 log.error(f"Error creating/adding connection in undo: {e}", exc_info=True)
                         # else: log.debug(f" Connection already exists during restore: {from_node.id} -> {to_node.id}")
                     else: log.warning(f" Could not find sockets for restored connection: {conn_data}")
                 else: log.warning(f" Could not find nodes for restored connection: {conn_data}")


    def _create_macro_definition_and_analyze(self, selected_items):
        """Створює визначення макросу та аналізує зовнішні зв'язки."""
        # --- Запит імені макросу ---
        macro_name_input, ok = QInputDialog.getText(
            self.main_window,
            "Створення Макросу",
            "Введіть ім'я для нового макросу:",
            QLineEdit.EchoMode.Normal,
            f"Макрос {len(self.main_window.project_data.get('macros', {})) + 1}"
        )
        if not ok or not macro_name_input.strip():
             log.warning("Macro creation cancelled by user.")
             return None, None # Скасовуємо створення
        macro_name = macro_name_input.strip()
        macro_id = generate_short_id()
        # --- Кінець запиту імені ---

        log.debug(f"Generating macro definition. ID: {macro_id}, Name: {macro_name}")

        selected_nodes = {item for item in selected_items if isinstance(item, BaseNode)}
        selected_node_ids = {node.id for node in selected_nodes}

        internal_nodes_data = []
        internal_connections_data = []
        macro_inputs = [] # {'name': 'In1', 'internal_node_id': 'xyz', 'internal_socket_name': 'in', 'macro_input_node_id': 'guid'}
        macro_outputs = []# {'name': 'Out1', 'internal_node_id': 'abc', 'internal_socket_name': 'out', 'macro_output_node_id': 'guid'}
        external_connections_info = [] # Для перепідключення в redo/undo

        old_id_to_new_id = {}
        all_scene_connections = [item for item in self.scene.items() if isinstance(item, Connection)]

        # 1. Копіюємо вузли та генеруємо нові ID
        log.debug("Copying internal nodes...")
        bounding_rect = QRectF()
        min_x, min_y = float('inf'), float('inf')
        node_positions = [] # Зберігаємо позиції для розрахунку центру
        try: # Додаємо try-except для цього блоку
            for node in selected_nodes:
                node_rect = node.sceneBoundingRect()
                node_pos = node.pos()
                node_positions.append(node_pos) # Додаємо позицію для розрахунку центру пізніше

                if not bounding_rect.isValid(): bounding_rect = node_rect
                else: bounding_rect = bounding_rect.united(node_rect)

                min_x = min(min_x, node_pos.x())
                min_y = min(min_y, node_pos.y())

                new_id = generate_short_id()
                old_id_to_new_id[node.id] = new_id
                node_data = node.to_data()
                node_data['id'] = new_id
                internal_nodes_data.append(node_data) # Зберігаємо дані з оригінальними позиціями поки що

            # Перевірка, чи вдалося обчислити min_x, min_y
            if min_x == float('inf') or min_y == float('inf'):
                 # Якщо вибрано лише один елемент без валідних меж, може статися помилка
                 if len(selected_nodes) == 1:
                      # Використовуємо позицію єдиного вузла
                      single_node = list(selected_nodes)[0]
                      min_x = single_node.pos().x()
                      min_y = single_node.pos().y()
                      log.warning("Bounding rect calculation failed for single node, using node position as min_x/min_y.")
                 else:
                      log.error("Could not determine bounds of selected nodes (min_x/min_y is inf). Aborting macro creation.")
                      return None, None

            log.debug(f"Calculated bounds: min_x={min_x:.1f}, min_y={min_y:.1f}, width={bounding_rect.width():.1f}, height={bounding_rect.height():.1f}")

            # Нормалізуємо позиції ТІЛЬКИ після обчислення min_x, min_y
            for node_data in internal_nodes_data:
                 original_pos_x, original_pos_y = node_data['pos']
                 node_data['pos'] = (original_pos_x - min_x, original_pos_y - min_y)
                 log.debug(f"  Node {node_data['node_type']} original pos ({original_pos_x:.1f}, {original_pos_y:.1f}), normalized to ({node_data['pos'][0]:.1f}, {node_data['pos'][1]:.1f})")

        except Exception as e:
             log.error(f"Error processing selected nodes: {e}", exc_info=True)
             return None, None


        # Розраховуємо центр для розміщення MacroInput/Output (використовуємо нормалізовані координати bounding_rect)
        normalized_bounding_rect = bounding_rect.translated(-min_x, -min_y)
        center_x = normalized_bounding_rect.center().x()
        center_y = normalized_bounding_rect.center().y()
        normalized_width = normalized_bounding_rect.width()
        log.debug(f"Normalized center: ({center_x:.1f}, {center_y:.1f}), Normalized width: {normalized_width:.1f}")


        input_count = 0
        output_count = 0

        # 2. Аналізуємо з'єднання
        log.debug("Analyzing connections...")
        try: # Додаємо try-except для аналізу з'єднань
            for conn in all_scene_connections:
                start_node = conn.start_socket.parentItem() if conn.start_socket else None
                end_node = conn.end_socket.parentItem() if conn.end_socket else None
                if not start_node or not end_node: continue

                start_in_selection = start_node.id in selected_node_ids
                end_in_selection = end_node.id in selected_node_ids

                if start_in_selection and end_in_selection:
                    # Внутрішнє з'єднання
                    conn_data = conn.to_data()
                    new_from_id = old_id_to_new_id.get(conn_data['from_node'])
                    new_to_id = old_id_to_new_id.get(conn_data['to_node'])
                    if new_from_id and new_to_id:
                         conn_data['from_node'] = new_from_id
                         conn_data['to_node'] = new_to_id
                         conn_data['to_socket'] = conn.end_socket.socket_name # Зберігаємо цільовий сокет
                         internal_connections_data.append(conn_data)
                elif not start_in_selection and end_in_selection:
                    # Вхідне з'єднання
                    input_count += 1
                    input_name = f"Вхід {input_count}"
                    macro_input_node = MacroInputNode(name=input_name)
                    # Розміщуємо вузол входу відносно нормалізованих координат
                    input_x = -macro_input_node.width - 50 # Зліва
                    # Розподіляємо вертикально відносно центру bounding_rect
                    input_y = center_y + (input_count - (len(macro_inputs) + 1 + 1) / 2) * 70 # Додаємо +1 для кращого розподілу
                    macro_input_node.setPos(input_x, input_y)
                    input_node_data = macro_input_node.to_data()
                    # Перезаписуємо позицію в даних, бо setPos міг не оновити її там
                    input_node_data['pos'] = (input_x, input_y)
                    internal_nodes_data.append(input_node_data)
                    log.debug(f"  Created MacroInputNode '{input_name}' at ({input_x:.1f}, {input_y:.1f})")


                    internal_target_node_id = old_id_to_new_id.get(end_node.id)
                    internal_target_socket_name = conn.end_socket.socket_name
                    input_info = {
                        'name': input_name,
                        'internal_node_id': internal_target_node_id,
                        'internal_socket_name': internal_target_socket_name,
                        'macro_input_node_id': input_node_data['id']
                    }
                    macro_inputs.append(input_info)

                    internal_connections_data.append({
                         'from_node': input_node_data['id'], 'from_socket': 'out',
                         'to_node': internal_target_node_id, 'to_socket': internal_target_socket_name
                    })
                    external_connections_info.append({
                         'type': 'input', 'original_conn_data': conn.to_data(), 'target_input_name': input_name,
                         'original_conn': conn # Зберігаємо сам об'єкт для видалення
                    })
                elif start_in_selection and not end_in_selection:
                     # Вихідне з'єднання
                    output_count += 1
                    output_name = f"Вихід {output_count}"
                    macro_output_node = MacroOutputNode(name=output_name)
                    # Розміщуємо вузол виходу відносно нормалізованих координат
                    output_x = normalized_width + 50 # Справа
                    output_y = center_y + (output_count - (len(macro_outputs) + 1 + 1) / 2) * 70 # Розподіляємо вертикально
                    macro_output_node.setPos(output_x, output_y)
                    output_node_data = macro_output_node.to_data()
                    output_node_data['pos'] = (output_x, output_y) # Перезаписуємо позицію
                    internal_nodes_data.append(output_node_data)
                    log.debug(f"  Created MacroOutputNode '{output_name}' at ({output_x:.1f}, {output_y:.1f})")


                    internal_source_node_id = old_id_to_new_id.get(start_node.id)
                    internal_source_socket_name = conn.start_socket.socket_name
                    output_info = {
                        'name': output_name,
                        'internal_node_id': internal_source_node_id,
                        'internal_socket_name': internal_source_socket_name,
                        'macro_output_node_id': output_node_data['id']
                    }
                    macro_outputs.append(output_info)

                    internal_connections_data.append({
                         'from_node': internal_source_node_id, 'from_socket': internal_source_socket_name,
                         'to_node': output_node_data['id'], 'to_socket': 'in'
                    })
                    external_connections_info.append({
                        'type': 'output', 'original_conn_data': conn.to_data(), 'source_output_name': output_name,
                        'original_conn': conn # Зберігаємо сам об'єкт для видалення
                    })
        except Exception as e:
            log.error(f"Error analyzing connections: {e}", exc_info=True)
            return None, None

        # 3. Створюємо словник визначення макросу
        macro_data = {
            'id': macro_id, 'name': macro_name,
            'nodes': internal_nodes_data, 'connections': internal_connections_data,
            'inputs': macro_inputs, 'outputs': macro_outputs
        }

        # 4. Додаємо визначення до проекту
        self.main_window.project_data.setdefault('macros', {})[macro_id] = macro_data
        log.debug(f"Macro definition '{macro_id}' created and added to project data.")

        return macro_data, external_connections_info

    def _create_and_add_macro_node(self, macro_data=None):
         """Створює MacroNode, оновлює сокети та додає на сцену."""
         if not self.macro_id:
              log.error("Cannot create MacroNode: Macro ID is not set.")
              self.setObsolete(True)
              return None

         # Отримуємо дані макросу, якщо вони не передані (для повторного redo)
         if not macro_data:
              macro_data = self.main_window.project_data.get('macros', {}).get(self.macro_id)
              if not macro_data:
                   log.error(f"Cannot create MacroNode: Macro definition {self.macro_id} not found.")
                   self.setObsolete(True)
                   return None

         # Розраховуємо позицію для MacroNode (центр виділених елементів)
         center_pos = QPointF(0, 0)
         # Використовуємо ЗБЕРЕЖЕНІ дані для розрахунку центру
         node_positions = [QPointF(*item_info['data']['pos']) for item_info in self.removed_items_data if item_info['type'] == 'node']
         if node_positions:
             try:
                 center_x = sum(p.x() for p in node_positions) / len(node_positions)
                 center_y = sum(p.y() for p in node_positions) / len(node_positions)
                 center_pos = QPointF(center_x, center_y)
             except ZeroDivisionError:
                  log.warning("Cannot calculate center position for MacroNode - no node positions found in removed data.")
                  # Використовуємо позицію першого вузла або (0,0)
                  if self.removed_items_data: center_pos = QPointF(*self.removed_items_data[0]['data'].get('pos', (0,0)))
         log.debug(f"Calculated center position for MacroNode: ({center_pos.x():.1f}, {center_pos.y():.1f})")


         # Створюємо вузол
         try:
             macro_node = MacroNode(macro_id=self.macro_id, name=macro_data['name'])
             self.macro_node_id = macro_node.id # Зберігаємо ID створеного вузла
             macro_node.setPos(center_pos)

             # Оновлюємо сокети та додаємо на сцену
             macro_node.update_sockets_from_definition(macro_data)
             self.scene.addItem(macro_node)
             self.scene.clearSelection()
             macro_node.setSelected(True)
             log.debug(f"Created and added MacroNode {self.macro_node_id} at {center_pos}")
             return macro_node
         except Exception as e:
              log.error(f"Error creating or adding MacroNode: {e}", exc_info=True)
              self.setObsolete(True)
              return None


    def _reconnect_external_connections(self, macro_node, external_connections_info=None):
        """Перепідключає зовнішні з'єднання до MacroNode."""
        if external_connections_info is None:
             external_connections_info = self.external_connections_data # Використовуємо збережені дані

        log.debug(f"Reconnecting {len(external_connections_info)} external connections to MacroNode {macro_node.id}...")
        new_connections_refs = [] # Зберігаємо посилання на сокети
        for info in external_connections_info:
            original_data = info['original_conn_data']
            new_conn = None
            try:
                if info['type'] == 'input':
                    # Зовнішній вузол -> MacroNode
                    external_node_id = original_data['from_node']
                    external_socket_name = original_data['from_socket']
                    macro_socket_name = info['target_input_name'] # Ім'я входу макросу стало іменем сокету

                    external_node = next((item for item in self.scene.items() if isinstance(item, BaseNode) and item.id == external_node_id), None)
                    if external_node:
                        external_socket = external_node.get_socket(external_socket_name)
                        macro_socket = macro_node.get_socket(macro_socket_name)
                        if external_socket and macro_socket:
                            # Перевірка, чи з'єднання вже існує
                            already_exists = any(c.end_socket == macro_socket for c in external_socket.connections)
                            if not already_exists:
                                 new_conn = Connection(external_socket, macro_socket)
                                 log.debug(f"  Reconnected Input: {external_node_id}:{external_socket_name} -> Macro:{macro_socket_name}")
                            # else: log.debug(f" Input connection already exists.")
                        # else: log.warning(f"  Input reconnect failed: Sockets not found (ext: {external_socket}, macro: {macro_socket})")
                    # else: log.warning(f"  Input reconnect failed: External node {external_node_id} not found.")

                elif info['type'] == 'output':
                    # MacroNode -> Зовнішній вузол
                    external_node_id = original_data['to_node']
                    external_socket_name = original_data.get('to_socket', 'in') # TODO: Save 'to_socket'?
                    macro_socket_name = info['source_output_name']

                    external_node = next((item for item in self.scene.items() if isinstance(item, BaseNode) and item.id == external_node_id), None)
                    if external_node:
                        external_socket = external_node.get_socket(external_socket_name)
                        macro_socket = macro_node.get_socket(macro_socket_name)
                        if external_socket and macro_socket:
                            # Перевірка, чи з'єднання вже існує
                            already_exists = any(c.end_socket == external_socket for c in macro_socket.connections)
                            if not already_exists:
                                 new_conn = Connection(macro_socket, external_socket)
                                 log.debug(f"  Reconnected Output: Macro:{macro_socket_name} -> {external_node_id}:{external_socket_name}")
                            # else: log.debug(f" Output connection already exists.")
                        # else: log.warning(f"  Output reconnect failed: Sockets not found (macro: {macro_socket}, ext: {external_socket})")
                    # else: log.warning(f"  Output reconnect failed: External node {external_node_id} not found.")

                if new_conn:
                    self.scene.addItem(new_conn)
                    new_connections_refs.append({ # Зберігаємо посилання
                        'start_ref': {'node_id': new_conn.start_socket.parentItem().id, 'socket_name': new_conn.start_socket.socket_name},
                        'end_ref': {'node_id': new_conn.end_socket.parentItem().id, 'socket_name': new_conn.end_socket.socket_name}
                    })

            except Exception as e:
                 log.error(f" Error reconnecting external connection {info}: {e}", exc_info=True)

        return new_connections_refs # Повертаємо список посилань

