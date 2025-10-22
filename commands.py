import uuid
from lxml import etree as ET
from PyQt6.QtGui import QUndoCommand
from PyQt6.QtCore import QPointF, QRectF

from nodes import BaseNode, Connection, CommentItem, FrameItem, TriggerNode, NODE_REGISTRY, generate_short_id


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
        main_window = self.scene.views()[0].parent()
        self.node.update_display_properties(main_window.project_data.get('config'))

    def redo(self):
        self.scene.addItem(self.node)
        self.scene.clearSelection()
        self.node.setSelected(True)

    def undo(self):
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

    def redo(self):
        self.scene.addItem(self.new_node)

        start_node = next(
            (item for item in self.scene.items() if isinstance(item, BaseNode) and item.id == self.start_node_id), None)
        if not start_node:
            self.setObsolete(True)
            return

        start_socket = start_node.get_socket(self.start_socket_name)
        end_socket = self.new_node.in_socket

        if not (start_socket and end_socket):
            self.setObsolete(True)
            return

        # The new node should always be connected via its input socket
        is_output_from_start = True  # This command is only triggered from output sockets

        if self.connection is None:
            if is_output_from_start:
                self.connection = Connection(start_socket, end_socket)
            else:  # This case should not happen with the current UI logic
                self.connection = Connection(end_socket, start_socket)
        else:
            # Re-establish connections if they were broken during undo
            self.connection.start_socket.add_connection(self.connection)
            self.connection.end_socket.add_connection(self.connection)

        self.scene.addItem(self.connection)
        self.connection.update_path()

        main_window = self.scene.views()[0].parent()
        self.new_node.update_display_properties(main_window.project_data.get('config'))

        self.scene.clearSelection()
        self.new_node.setSelected(True)

    def undo(self):
        if self.connection:
            start_socket = self.connection.start_socket
            end_socket = self.connection.end_socket
            start_socket.remove_connection(self.connection)
            end_socket.remove_connection(self.connection)
            if self.connection.scene():
                self.scene.removeItem(self.connection)

        if self.new_node.scene():
            self.scene.removeItem(self.new_node)


class AddCommentCommand(QUndoCommand):
    def __init__(self, scene, position, view, parent=None):
        super().__init__(parent)
        self.scene = scene
        self.position = position
        self.comment_item = CommentItem(view=view)
        self.comment_item.setPos(position)
        self.setText("Додати коментар")

    def redo(self):
        self.scene.addItem(self.comment_item)
        self.scene.clearSelection()
        self.comment_item.setSelected(True)

    def undo(self):
        self.scene.removeItem(self.comment_item)


class ResizeCommand(QUndoCommand):
    def __init__(self, item, old_dims, new_dims, parent=None):
        super().__init__(parent)
        self.item = item
        self.old_dims = old_dims
        self.new_dims = new_dims
        item_type = "коментаря" if isinstance(item, CommentItem) else "фрейму" if isinstance(item,
                                                                                             FrameItem) else "елемента"
        self.setText(f"Змінити розмір {item_type}")

    def redo(self):
        self.item.set_dimensions(self.new_dims[0], self.new_dims[1])

    def undo(self):
        self.item.set_dimensions(self.old_dims[0], self.old_dims[1])


class AlignNodesCommand(QUndoCommand):
    def __init__(self, nodes, mode, parent=None):
        super().__init__(parent)
        self.nodes = list(nodes)  # Make a copy
        self.mode = mode
        self.old_positions = {node: node.pos() for node in self.nodes}
        self.setText("Вирівняти вузли")

    def redo(self):
        if len(self.nodes) < 2:
            return

        # Determine the target based on the mode
        if self.mode == 'left':
            target_node = min(self.nodes, key=lambda n: n.sceneBoundingRect().left())
            align_pos = target_node.sceneBoundingRect().left()
            for node in self.nodes:
                if node is not target_node:
                    node.setX(align_pos)
        elif self.mode == 'right':
            target_node = max(self.nodes, key=lambda n: n.sceneBoundingRect().right())
            align_pos = target_node.sceneBoundingRect().right()
            for node in self.nodes:
                if node is not target_node:
                    node.setX(align_pos - node.sceneBoundingRect().width())
        elif self.mode == 'h_center':
            # Use the average of centers for horizontal alignment
            avg_center_x = sum(n.sceneBoundingRect().center().x() for n in self.nodes) / len(self.nodes)
            for node in self.nodes:
                node.setX(avg_center_x - node.sceneBoundingRect().width() / 2)
        elif self.mode == 'top':
            target_node = min(self.nodes, key=lambda n: n.sceneBoundingRect().top())
            align_pos = target_node.sceneBoundingRect().top()
            for node in self.nodes:
                if node is not target_node:
                    node.setY(align_pos)
        elif self.mode == 'bottom':
            target_node = max(self.nodes, key=lambda n: n.sceneBoundingRect().bottom())
            align_pos = target_node.sceneBoundingRect().bottom()
            for node in self.nodes:
                if node is not target_node:
                    node.setY(align_pos - node.sceneBoundingRect().height())
        elif self.mode == 'v_center':
            # Use the average of centers for vertical alignment
            avg_center_y = sum(n.sceneBoundingRect().center().y() for n in self.nodes) / len(self.nodes)
            for node in self.nodes:
                node.setY(avg_center_y - node.sceneBoundingRect().height() / 2)

    def undo(self):
        for node, pos in self.old_positions.items():
            node.setPos(pos)


class RemoveItemsCommand(QUndoCommand):
    def __init__(self, scene, items, parent=None):
        super().__init__(parent)
        self.scene = scene
        self.items_data = []

        items_to_remove = set(items)

        nodes_to_remove = {item for item in items_to_remove if isinstance(item, BaseNode)}

        for node in nodes_to_remove:
            for socket in node.get_all_sockets():
                for conn in socket.connections:
                    items_to_remove.add(conn)

        for item in items_to_remove:
            if isinstance(item, BaseNode):
                self.items_data.append({'type': 'node', 'item': item})
            elif isinstance(item, (CommentItem, FrameItem)):
                self.items_data.append({'type': 'container', 'item': item})
            elif isinstance(item, Connection):
                start_node = item.start_socket.parentItem()
                end_node = item.end_socket.parentItem()
                if start_node and end_node:
                    self.items_data.append({
                        'type': 'connection', 'item': item,
                        'from_id': start_node.id,
                        'from_socket_name': item.start_socket.socket_name,
                        'to_id': end_node.id
                    })

        self.setText("Видалити елементи")

    def redo(self):
        for data in self.items_data:
            if data['type'] == 'connection':
                conn = data['item']
                conn.start_socket.remove_connection(conn)
                conn.end_socket.remove_connection(conn)
                if conn.scene(): self.scene.removeItem(conn)

        for data in self.items_data:
            if data['type'] in ('node', 'container'):
                if data['item'].scene(): self.scene.removeItem(data['item'])

    def undo(self):
        nodes_map = {}
        for data in self.items_data:
            if data['type'] in ('node', 'container'):
                item = data['item']
                self.scene.addItem(item)
                if data['type'] == 'node':
                    nodes_map[item.id] = item

        for data in self.items_data:
            if data['type'] == 'connection':
                conn = data['item']
                from_node = nodes_map.get(data['from_id'])
                to_node = nodes_map.get(data['to_id'])
                if not from_node:
                    from_node = next(
                        (i for i in self.scene.items() if isinstance(i, BaseNode) and i.id == data['from_id']), None)
                if not to_node:
                    to_node = next((i for i in self.scene.items() if isinstance(i, BaseNode) and i.id == data['to_id']),
                                   None)

                if from_node and to_node:
                    start_socket = from_node.get_socket(data['from_socket_name'])
                    end_socket = to_node.in_socket
                    if start_socket and end_socket:
                        conn.start_socket = start_socket
                        conn.end_socket = end_socket
                        conn.start_socket.add_connection(conn)
                        conn.end_socket.add_connection(conn)
                        self.scene.addItem(conn)
                        conn.update_path()


class MoveItemsCommand(QUndoCommand):
    def __init__(self, items_map, parent=None):
        super().__init__(parent)
        self.scene = list(items_map.keys())[0].scene() if items_map else None
        self.items_data = []
        for item, (old_pos, new_pos) in items_map.items():
            self.items_data.append({'item': item, 'old_pos': old_pos, 'new_pos': new_pos})
        self.setText("Перемістити елементи")

    def _apply_pos(self, pos_key):
        if not self.scene: return
        for data in self.items_data:
            item = data['item']
            if item.scene():
                item.setPos(data[pos_key])

    def redo(self):
        self._apply_pos('new_pos')

    def undo(self):
        self._apply_pos('old_pos')


class AddConnectionCommand(QUndoCommand):
    def __init__(self, scene, start_socket, end_socket, parent=None):
        super().__init__(parent)
        self.scene = scene
        self.start_socket = start_socket
        self.end_socket = end_socket
        self.connection = None
        self.setText("Додати з'єднання")

    def redo(self):
        if self.connection is None:
            self.connection = Connection(self.start_socket, self.end_socket)
        else:
            self.start_socket.add_connection(self.connection)
            self.end_socket.add_connection(self.connection)
        self.scene.addItem(self.connection)
        self.connection.update_path()
        self.scene.clearSelection()
        self.connection.setSelected(True)

    def undo(self):
        self.start_socket.remove_connection(self.connection)
        self.end_socket.remove_connection(self.connection)
        self.scene.removeItem(self.connection)


class ChangePropertiesCommand(QUndoCommand):
    def __init__(self, node, old_data, new_data, parent=None):
        super().__init__(parent)
        self.node = node
        self.old_data = old_data
        self.new_data = new_data
        self.setText("Змінити властивості")

    def _apply_data(self, data):
        self.node.node_name = data['name']
        self.node.description = data['desc']
        self.node.properties = data['props']

        main_window = self.node.scene().views()[0].parent()
        config = main_window.project_data.get('config')

        self.node.update_display_properties(config)

        if main_window.current_selected_node == self.node:
            main_window.on_selection_changed()

    def redo(self): self._apply_data(self.new_data)

    def undo(self): self._apply_data(self.old_data)


class PasteCommand(QUndoCommand):
    def __init__(self, scene, clipboard_text, position, parent=None):
        super().__init__(parent)
        self.scene = scene
        self.clipboard_text = clipboard_text
        self.position = position
        self.pasted_items = []
        self.setText("Вставити елементи")

    def redo(self):
        if self.pasted_items:
            for item in self.pasted_items:
                self.scene.addItem(item)
                if isinstance(item, Connection):
                    item.start_socket.add_connection(item)
                    item.end_socket.add_connection(item)
            self.scene.clearSelection()
            for item in self.pasted_items:
                if isinstance(item, BaseNode): item.setSelected(True)
            return

        try:
            clipboard_root = ET.fromstring(self.clipboard_text.encode('utf-8'))
            nodes_xml = clipboard_root.find("nodes")
            if nodes_xml is None: self.setObsolete(True); return

            old_to_new_id_map, new_nodes_map = {}, {}
            node_elements = list(nodes_xml)
            if not node_elements: self.setObsolete(True); return

            min_x = min((float(el.get("x")) for el in node_elements))
            min_y = min((float(el.get("y")) for el in node_elements))
            ref_pos = QPointF(min_x, min_y)

            for node_el in node_elements:
                node_data = BaseNode.data_from_xml(node_el)
                new_node = BaseNode.from_data(node_data)
                old_id = node_data['id']
                new_id = generate_short_id() # Використовуємо короткий ID
                new_node.id = new_id
                old_to_new_id_map[old_id] = new_id
                original_pos = QPointF(*node_data['pos'])
                new_node.setPos(self.position + (original_pos - ref_pos))
                self.pasted_items.append(new_node)
                new_nodes_map[new_id] = new_node

            connections_xml = clipboard_root.find("connections")
            if connections_xml is not None:
                for conn_el in connections_xml:
                    conn_data = Connection.data_from_xml(conn_el)
                    from_node = new_nodes_map.get(old_to_new_id_map.get(conn_data['from_node']))
                    to_node = new_nodes_map.get(old_to_new_id_map.get(conn_data['to_node']))

                    if from_node and to_node:
                        from_socket = from_node.get_socket(conn_data['from_socket'])
                        to_socket = to_node.in_socket
                        if from_socket and to_socket:
                            conn = Connection(from_socket, to_socket)
                            self.pasted_items.append(conn)

            main_window = self.scene.views()[0].parent()
            config = main_window.project_data.get('config')
            self.scene.clearSelection()
            for item in self.pasted_items:
                self.scene.addItem(item)
                if isinstance(item, BaseNode):
                    item.setSelected(True)
                    item.update_display_properties(config)

        except Exception:
            self.setObsolete(True)

    def undo(self):
        for item in self.pasted_items:
            if isinstance(item, Connection):
                item.start_socket.remove_connection(item)
                item.end_socket.remove_connection(item)
            if item.scene(): self.scene.removeItem(item)


class AddFrameCommand(QUndoCommand):
    def __init__(self, scene, items, parent=None):
        super().__init__(parent)
        self.scene = scene
        self.items = items
        self.frame = None
        self.setText("Сгруппировать в фрейм")

    def redo(self):
        if not self.items:
            self.setObsolete(True)
            return

        if self.frame is None:
            bounding_rect = QRectF()
            for item in self.items:
                bounding_rect = bounding_rect.united(item.sceneBoundingRect())

            padding = 20
            frame_rect = bounding_rect.adjusted(-padding, -padding, padding, padding)
            self.frame = FrameItem(width=frame_rect.width(), height=frame_rect.height())
            self.frame.setPos(frame_rect.topLeft())

        self.scene.clearSelection()
        self.scene.addItem(self.frame)
        self.frame.setSelected(True)

    def undo(self):
        if self.frame and self.frame.scene():
            self.scene.removeItem(self.frame)
        self.scene.clearSelection()
        for item in self.items:
            item.setSelected(True)


class UngroupFrameCommand(QUndoCommand):
    def __init__(self, scene, frame, parent=None):
        super().__init__(parent)
        self.scene = scene
        self.frame = frame
        self.setText("Разгруппировать фрейм")

    def redo(self):
        if self.frame and self.frame.scene():
            self.scene.removeItem(self.frame)
        self.scene.clearSelection()

    def undo(self):
        if self.frame and not self.frame.scene():
            self.scene.addItem(self.frame)
        self.scene.clearSelection()
        self.frame.setSelected(True)


