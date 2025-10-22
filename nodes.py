import uuid
import logging # Додано для логування
from enum import Enum, auto
from lxml import etree as ET
from PyQt6.QtGui import QColor, QPen, QBrush, QFont, QPainterPath, QTextCursor
from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtWidgets import QGraphicsItem, QGraphicsRectItem, QGraphicsTextItem, QGraphicsEllipseItem, QGraphicsPathItem

log = logging.getLogger(__name__) # Створюємо логгер для цього модуля

def generate_short_id():
    """
    Генерує лаконічний 12-символьний унікальний ID.
    """
    return uuid.uuid4().hex[:12]


class Connection(QGraphicsPathItem):
    def __init__(self, start_socket, end_socket):
        super().__init__()
        self.start_socket, self.end_socket = start_socket, end_socket
        self.default_pen = QPen(QColor("#a2a2a2"), 2)
        self.selected_pen = QPen(QColor("#fffc42"), 3)
        self.active_pen = QPen(QColor(93, 173, 226), 2.5)
        self.setPen(self.default_pen)
        self.setZValue(0)
        self.start_socket.add_connection(self)
        self.end_socket.add_connection(self)
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.update_path()

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedChange:
            self.setPen(self.selected_pen if value else self.default_pen)
        return super().itemChange(change, value)

    def set_active_state(self, active):
        if active:
            self.setPen(self.active_pen)
        else:
            is_selected = self.isSelected()
            self.setPen(self.selected_pen if is_selected else self.default_pen)

    def update_path(self):
        # Перевірка чи сокети ще існують і прив'язані до сцени
        if not self.start_socket or not self.end_socket or \
           not self.start_socket.scene() or not self.end_socket.scene():
            log.warning("Connection.update_path(): Invalid sockets found.")
            # Можливо, варто видалити з'єднання зі сцени, якщо сокети недійсні?
            # if self.scene(): self.scene().removeItem(self)
            return

        p1, p2 = self.start_socket.scenePos(), self.end_socket.scenePos()
        path = QPainterPath(p1)

        # Розрахунок контрольних точок для кривої Безьє
        dx = p2.x() - p1.x()
        dy = p2.y() - p1.y()

        # Стандартні контрольні точки (вертикальні)
        ctrl1 = p1 + QPointF(0, dy * 0.5)
        ctrl2 = p2 - QPointF(0, dy * 0.5)

        # Якщо вузли дуже близько по вертикалі, робимо S-подібну криву
        threshold = 50 # Емпіричний поріг
        if abs(dy) < threshold:
             offset_x = max(50, abs(dx) * 0.2) # Горизонтальне зміщення
             ctrl1 = p1 + QPointF(offset_x, threshold)
             ctrl2 = p2 - QPointF(offset_x, threshold)

        path.cubicTo(ctrl1, ctrl2, p2)
        self.setPath(path)


    def to_data(self):
        start_node = self.start_socket.parentItem()
        end_node = self.end_socket.parentItem()
        if start_node and end_node:
            # Check if parent nodes still exist (important for undo/redo)
            if start_node.scene() and end_node.scene():
                return {
                    'from_node': start_node.id,
                    'from_socket': self.start_socket.socket_name,
                    'to_node': end_node.id
                }
        log.warning(f"Connection.to_data(): Invalid connection detected (start={start_node}, end={end_node})")
        return {} # Return empty if connection is invalid

    def to_xml(self, parent_element):
        data = self.to_data()
        if data:
            ET.SubElement(parent_element, "connection", **data)

    @staticmethod
    def data_from_xml(xml_element):
        return {
            'from_node': xml_element.get("from_node"),
            'from_socket': xml_element.get("from_socket", "out"),  # Fallback for old format
            'to_node': xml_element.get("to_node")
        }

    @classmethod
    def from_data(cls, data):
        return data

    @staticmethod
    def data_to_xml(parent_element, conn_data):
        # Ensure all values are strings, provide defaults
        attrs = {
            "from_node": str(conn_data.get('from_node', '')),
            "from_socket": str(conn_data.get('from_socket', 'out')),
            "to_node": str(conn_data.get('to_node', ''))
        }
        ET.SubElement(parent_element, "connection", **attrs)


class Socket(QGraphicsEllipseItem):
    def __init__(self, parent_node, socket_name="in", is_output=False):
        super().__init__(-6, -6, 12, 12, parent_node)
        self.is_output, self.connections = is_output, []
        self.socket_name = socket_name # e.g., "in", "out", "out_true", "macro_in_1", "macro_out_exec"
        self.default_brush = QBrush(QColor("#d4d4d4"))
        self.hover_brush = QBrush(QColor("#77dd77"))
        self.is_highlighted = False
        self.setBrush(self.default_brush);
        self.setPen(QPen(QColor("#3f3f3f"), 2))
        self.setAcceptHoverEvents(True)
        self.setZValue(2)

    def hoverEnterEvent(self, event):
        view = self.scene().views()[0] if self.scene().views() else None
        if view and not view.start_socket:
            self.setBrush(self.hover_brush)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        if not self.is_highlighted:
            self.setBrush(self.default_brush)
        super().hoverLeaveEvent(event)

    def set_highlight(self, highlight):
        if self.is_highlighted == highlight:
            return
        self.is_highlighted = highlight
        self.setBrush(self.hover_brush if highlight else self.default_brush)
        self.update()

    def add_connection(self, connection):
        if connection not in self.connections:
            self.connections.append(connection)

    def remove_connection(self, connection):
        if connection in self.connections:
            self.connections.remove(connection)


class BaseNode(QGraphicsItem):
    # node_type тут тепер зберігає display name для UI
    def __init__(self, name="Вузол", node_type="Base", color=QColor("#4A90E2"), icon="●"):
        super().__init__()
        self.id = generate_short_id() # Используем короткий ID
        self._node_name = name if name is not None else "Вузол" # Ensure string
        self._description = ""
        self.node_type = node_type # This should be the display name (key in NODE_REGISTRY)
        self.node_color = color
        self.node_icon = icon
        self.width, self.height = 180, 85
        self.properties = []
        self._sockets = {} # Dictionary to store sockets {socket_name: Socket}
        self.setFlags(self.flags() | QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                      QGraphicsItem.GraphicsItemFlag.ItemSendsScenePositionChanges)
        self.setZValue(1)
        self.active_pen = QPen(QColor(93, 173, 226), 2, Qt.PenStyle.DashLine)
        self._create_elements();
        self._create_sockets() # Now calls the specific implementation if overridden
        self._create_validation_indicator()

    @property
    def node_name(self):
        return self._node_name

    @node_name.setter
    def node_name(self, value):
        self._node_name = value if value is not None else "" # Ensure string
        if hasattr(self, 'name_text'): # Check if element exists
             self.name_text.setPlainText(self._node_name)

    @property
    def description(self):
        return self._description

    @description.setter
    def description(self, value):
        self._description = value if value is not None else "" # Ensure string

    def _create_elements(self):
        self.rect = QGraphicsRectItem(0, 0, self.width, self.height, self)
        self.rect.setBrush(self.node_color);
        self.rect.setPen(QPen(Qt.GlobalColor.black, 1))

        self.icon_text = QGraphicsTextItem(self.node_icon, self)
        self.icon_text.setDefaultTextColor(Qt.GlobalColor.white);
        self.icon_text.setFont(QFont("Arial", 16))
        self.icon_text.setPos(8, 4)

        # Use self.node_type (display name) for the type text
        self.type_text = QGraphicsTextItem(self.node_type, self)
        self.type_text.setDefaultTextColor(Qt.GlobalColor.white);
        self.type_text.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        self.type_text.setPos(40, 8)

        self.name_text = QGraphicsTextItem(self.node_name, self)
        self.name_text.setDefaultTextColor(QColor("#f0f0f0"));
        self.name_text.setFont(QFont("Arial", 9, QFont.Weight.Bold));
        self.name_text.setPos(8, 35)

        self.properties_text = QGraphicsTextItem("", self)
        self.properties_text.setDefaultTextColor(QColor("#cccccc"));
        self.properties_text.setFont(QFont("Arial", 8))
        self.properties_text.setPos(8, 55)

    def add_socket(self, name, is_output=False, position=None):
        """Adds a socket to the node."""
        if name in self._sockets:
            # log.warning(f"Socket '{name}' already exists on node {self.id}. Returning existing.")
            return self._sockets[name]
        socket = Socket(self, socket_name=name, is_output=is_output)
        if position:
            socket.setPos(position)
        self._sockets[name] = socket
        return socket

    def get_socket(self, name):
        """Retrieves a socket by name."""
        return self._sockets.get(name)

    # Simplified access for common sockets (optional, for backward compatibility or convenience)
    @property
    def in_socket(self): return self.get_socket("in")
    @property
    def out_socket(self): return self.get_socket("out")
    @property
    def out_socket_true(self): return self.get_socket("out_true")
    @property
    def out_socket_false(self): return self.get_socket("out_false")
    @property
    def out_socket_loop(self): return self.get_socket("out_loop")
    @property
    def out_socket_end(self): return self.get_socket("out_end")

    def get_all_sockets(self):
        """Returns a list of all socket objects."""
        return list(self._sockets.values())

    def get_output_sockets(self):
        """Returns a list of all output socket objects."""
        return [sock for sock in self._sockets.values() if sock.is_output]

    def get_input_sockets(self):
        """Returns a list of all input socket objects."""
        return [sock for sock in self._sockets.values() if not sock.is_output]

    def _create_sockets(self):
        """Default socket creation for standard nodes."""
        if not isinstance(self, TriggerNode): # Trigger has no input
             self.add_socket("in", position=QPointF(self.width / 2, 0))
        # Most nodes have a single output by default
        if not isinstance(self, (ActivateOutputNode, DeactivateOutputNode, SendSMSNode, MacroOutputNode)):
            self.add_socket("out", is_output=True, position=QPointF(self.width / 2, self.height))

    def _create_validation_indicator(self):
        self.error_icon = QGraphicsTextItem("⚠️", self)
        self.error_icon.setFont(QFont("Arial", 12))
        self.error_icon.setPos(self.width - 24, 2)
        self.error_icon.setZValue(3)
        self.error_icon.setVisible(False)

    def set_validation_state(self, is_valid, message=""):
        self.error_icon.setVisible(not is_valid)
        self.error_icon.setToolTip(message)

    def set_active_state(self, active):
        if active:
            self.rect.setPen(self.active_pen)
        else:
            is_selected = self.isSelected()
            self.rect.setPen(QPen(QColor("#fffc42"), 2) if is_selected else QPen(Qt.GlobalColor.black, 1))

    def validate(self, config):
        self.set_validation_state(True)
        return True

    def update_display_properties(self, config=None):
        self.properties_text.setPlainText("")

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedChange:
            self.rect.setPen(QPen(QColor("#fffc42"), 2) if value else QPen(Qt.GlobalColor.black, 1))
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            for socket in self.get_all_sockets():
                # Check if connection list exists and socket is valid before iterating
                if hasattr(socket, 'connections') and socket.scene():
                    for conn in socket.connections:
                         # Ensure connection is also valid before updating
                         if conn and conn.scene():
                              conn.update_path()
        return super().itemChange(change, value)


    def boundingRect(self):
        extra = 10
        # Adjust bounding rect if node height is different
        rect_height = self.height if hasattr(self, 'height') else 85
        return QRectF(-extra, -extra, self.width + 2 * extra, rect_height + 2 * extra)

    def paint(self, painter, option, widget):
        pass # Base class does not paint itself, children elements do

    def to_data(self):
        # Зберігаємо ім'я класу
        class_name = self.__class__.__name__
        data = {'id': self.id, 'node_type': class_name, 'name': self.node_name,
                'description': self.description, 'pos': (self.pos().x(), self.pos().y()),
                'properties': self.properties}
        # Add macro specific data if needed
        if isinstance(self, MacroNode):
            data['macro_id'] = self.macro_id
        return data

    def to_xml(self, parent_element):
        return self.data_to_xml(parent_element, self.to_data())

    @staticmethod
    def data_from_xml(xml_element):
        # Читаємо атрибут 'type' як ім'я класу
        node_class_name = xml_element.get("type")
        data = {'id': xml_element.get("id"), 'node_type': node_class_name,
                'name': xml_element.get("name"), 'pos': (float(xml_element.get("x")), float(xml_element.get("y"))),
                'properties': []}
        desc_el = xml_element.find("description")
        data['description'] = desc_el.text if desc_el is not None else "" # Handle missing description
        props_el = xml_element.find("properties")
        if props_el is not None:
            for prop_el in props_el:
                key, value = prop_el.get("key"), prop_el.get("value")
                if key == 'zones':
                    value = value.split(',') if value else [] # Ensure list even if empty
                elif key in ('seconds', 'count'):
                    try:
                        value = int(value) if value is not None else 0
                    except (ValueError, TypeError):
                         log.warning(f"Could not convert property '{key}' value '{value}' to int for node {data.get('id')}. Using default.")
                         value = 0 # Or a suitable default for the specific property
                data['properties'].append((key, value))
        # Add macro specific data if needed
        if node_class_name == 'MacroNode': # Перевіряємо за ім'ям класу
            data['macro_id'] = xml_element.get('macro_id')
        return data

    @staticmethod
    def data_to_xml(parent_element, node_data):
        # Ensure all attribute values are strings and handle potential None
        attrs = {
            'id': str(node_data.get('id', '')),
            'type': str(node_data.get('node_type', '')), # Now saving class name
            'name': str(node_data.get('name', '')),
            'x': str(node_data.get('pos', [0, 0])[0]),
            'y': str(node_data.get('pos', [0, 0])[1])
        }
        # Add macro specific attributes, ensuring it's a string
        if node_data.get('node_type') == 'MacroNode': # Check against class name
            attrs['macro_id'] = str(node_data.get('macro_id', ''))

        node_el = ET.SubElement(parent_element, "node", **attrs)

        desc_el = ET.SubElement(node_el, "description");
        # Ensure description is a string
        desc_el.text = str(node_data.get('description', ''))

        # Ensure 'properties' exists and is a list before iterating
        properties = node_data.get('properties')
        if properties and isinstance(properties, list):
            props_el = ET.SubElement(node_el, "properties")
            for prop_item in properties:
                 # Check if prop_item is a tuple/list of size 2
                 if isinstance(prop_item, (list, tuple)) and len(prop_item) == 2:
                      key, value = prop_item
                      prop_attrs = {
                           "key": str(key) if key is not None else "",
                           # Convert value to string, handle lists specifically
                           "value": ",".join(map(str, value)) if isinstance(value, list) else str(value if value is not None else "")
                      }
                      ET.SubElement(props_el, "property", **prop_attrs)
                 else:
                      log.warning(f"Skipping invalid property item: {prop_item} for node {attrs.get('id')}")

        return node_el


    @classmethod
    def from_data(cls, data):
        node_class_name = data.get('node_type') # This is now the class name
        node_class = BaseNode # Default fallback

        # Find the class by iterating through registry values (classes)
        if node_class_name:
            for registry_class in NODE_REGISTRY.values():
                if registry_class.__name__ == node_class_name:
                    node_class = registry_class
                    break
            else: # If loop finished without break
                 log.warning(f"Node class '{node_class_name}' not found in NODE_REGISTRY. Using BaseNode.")

        # Create instance
        node = node_class() # Call constructor specific to the class

        # Set common attributes
        node.id = data.get('id', generate_short_id())
        # Use setters to ensure validation/updates if they exist
        node.node_name = data.get('name', '')
        node.description = data.get('description', '')
        node.setPos(QPointF(*data.get('pos', (0, 0))))
        # Ensure properties is a list
        loaded_properties = data.get('properties', [])
        node.properties = loaded_properties if isinstance(loaded_properties, list) else []

        # Set specific attributes (like macro_id)
        if isinstance(node, MacroNode):
            node.macro_id = data.get('macro_id')
            # TODO: Need to dynamically add sockets based on macro definition
            # This requires access to the full project data, usually done after initial loading

        return node


class TriggerNode(BaseNode):
    ICON = "⚡"

    def __init__(self):
        # Pass the display name to super()
        super().__init__(name="Тригер", node_type="Тригер", color=QColor("#c0392b"), icon=self.ICON)
        # Initialize properties *after* super().__init__ has run
        self.properties.append(('trigger_type', 'Пожежа'))
        self.properties.append(('zones', []))

    def _create_sockets(self):
        # Override: Trigger only has an output
        self.add_socket("out", is_output=True, position=QPointF(self.width / 2, self.height))

    def update_display_properties(self, config=None):
        props = dict(self.properties)
        trigger_type = props.get('trigger_type', 'N/A')
        zones = props.get('zones', [])
        text = f"{trigger_type}\nЗони: {len(zones)}"
        self.properties_text.setPlainText(text)

    def validate(self, config):
        props = dict(self.properties)
        zones_ids_to_check = props.get('zones', [])
        if not zones_ids_to_check:
            self.set_validation_state(False, "Не вибрано жодної зони для тригера.")
            return False

        if config:
            all_zone_ids = []
            for device in config.get('devices', []):
                all_zone_ids.extend([z['id'] for z in device.get('zones', [])])

            for zone_id in zones_ids_to_check:
                if zone_id not in all_zone_ids:
                    self.set_validation_state(False, f"Зона з ID '{zone_id}' не знайдена у конфігурації.")
                    return False

        self.set_validation_state(True)
        return True


class ActivateOutputNode(BaseNode):
    ICON = "🔊"

    def __init__(self):
        super().__init__(name="Активувати вихід", node_type="Дія", color=QColor("#27ae60"), icon=self.ICON)
        self.properties.append(('output_id', ''))

    def _create_sockets(self):
        # Override: Action nodes only have an input
        self.add_socket("in", position=QPointF(self.width / 2, 0))

    def update_display_properties(self, config=None):
        props = dict(self.properties)
        output_id = props.get('output_id')
        output_name = "НЕ ВИБРАНО"
        if config and output_id:
            all_outputs = []
            for device in config.get('devices', []):
                all_outputs.extend(device.get('outputs', []))

            for out in all_outputs:
                if out['id'] == output_id:
                    output_name = f"{out.get('parent_name', '')}: {out['name']}"
                    break
        self.properties_text.setPlainText(output_name)

    def validate(self, config):
        props = dict(self.properties)
        output_id = props.get('output_id')
        if not output_id:
            self.set_validation_state(False, "Не вибрано вихід для активації.")
            return False
        if config:
            all_ids = []
            for device in config.get('devices', []):
                all_ids.extend([o['id'] for o in device.get('outputs', [])])

            if output_id not in all_ids:
                self.set_validation_state(False, f"Вихід з ID '{output_id}' не знайдено.")
                return False

        self.set_validation_state(True)
        return True


class DeactivateOutputNode(BaseNode):
    ICON = "🔇"

    def __init__(self):
        super().__init__(name="Деактивувати вихід", node_type="Дія", color=QColor("#e74c3c"), icon=self.ICON)
        self.properties.append(('output_id', ''))

    def _create_sockets(self):
        # Override: Action nodes only have an input
        self.add_socket("in", position=QPointF(self.width / 2, 0))

    def update_display_properties(self, config=None):
        props = dict(self.properties)
        output_id = props.get('output_id')
        output_name = "НЕ ВИБРАНО"
        if config and output_id:
            all_outputs = []
            for device in config.get('devices', []):
                all_outputs.extend(device.get('outputs', []))

            for out in all_outputs:
                if out['id'] == output_id:
                    output_name = f"{out.get('parent_name', '')}: {out['name']}"
                    break
        self.properties_text.setPlainText(output_name)

    def validate(self, config):
        props = dict(self.properties)
        output_id = props.get('output_id')
        if not output_id:
            self.set_validation_state(False, "Не вибрано вихід для деактивації.")
            return False
        if config:
            all_ids = []
            for device in config.get('devices', []):
                all_ids.extend([o['id'] for o in device.get('outputs', [])])

            if output_id not in all_ids:
                self.set_validation_state(False, f"Вихід з ID '{output_id}' не знайдено.")
                return False

        self.set_validation_state(True)
        return True


class DelayNode(BaseNode):
    ICON = "⏳"

    def __init__(self):
        super().__init__(name="Затримка", node_type="Дія", color=QColor("#2980b9"), icon=self.ICON)
        self.properties.append(('seconds', 5))

    def update_display_properties(self, config=None):
        props = dict(self.properties)
        seconds = props.get('seconds', 0)
        self.properties_text.setPlainText(f"{seconds} сек.")


class SendSMSNode(BaseNode):
    ICON = "✉️"

    def __init__(self):
        super().__init__(name="Надіслати SMS", node_type="Дія", color=QColor("#9b59b6"), icon=self.ICON)
        self.properties.append(('user_id', ''))
        self.properties.append(('message', 'Тривога!'))

    def _create_sockets(self):
        # Override: Action nodes only have an input
        self.add_socket("in", position=QPointF(self.width / 2, 0))

    def update_display_properties(self, config=None):
        props = dict(self.properties)
        user_id = props.get('user_id')
        user_name = "НЕ ВИБРАНО"
        if config and user_id:
            for user in config.get('users', []):
                if user['id'] == user_id:
                    user_name = user['name']
                    break
        self.properties_text.setPlainText(f"Кому: {user_name}")

    def validate(self, config):
        props = dict(self.properties)
        user_id = props.get('user_id')
        message = props.get('message', '')
        if not user_id:
            self.set_validation_state(False, "Не вибрано користувача для відправки SMS.")
            return False
        if not message:
            self.set_validation_state(False, "Текст повідомлення не може бути порожнім.")
            return False
        if config:
            all_ids = [user['id'] for user in config.get('users', [])]
            if user_id not in all_ids:
                self.set_validation_state(False, f"Користувача з ID '{user_id}' не знайдено.")
                return False

        self.set_validation_state(True)
        return True


class ConditionNodeZoneState(BaseNode):
    ICON = "🔎"

    def __init__(self):
        super().__init__(name="Умова: Стан зони", node_type="Умова", color=QColor("#f39c12"), icon=self.ICON)
        self.properties.append(('zone_id', ''))
        self.properties.append(('state', 'Під охороною'))

    def _create_sockets(self):
        self.add_socket("in", position=QPointF(self.width / 2, 0))
        self.add_socket("out_true", is_output=True, position=QPointF(self.width * 0.25, self.height))
        self.add_socket("out_false", is_output=True, position=QPointF(self.width * 0.75, self.height))

        # Labels for sockets
        out_true_socket = self.get_socket("out_true")
        if out_true_socket:
            self.true_label = QGraphicsTextItem("Успіх", self)
            self.true_label.setDefaultTextColor(QColor("#aaffaa"))
            self.true_label.setFont(QFont("Arial", 7))
            self.true_label.setPos(out_true_socket.pos().x() - self.true_label.boundingRect().width() / 2,
                                   self.height - 14)

        out_false_socket = self.get_socket("out_false")
        if out_false_socket:
            self.false_label = QGraphicsTextItem("Невдача", self)
            self.false_label.setDefaultTextColor(QColor("#ffaaaa"))
            self.false_label.setFont(QFont("Arial", 7))
            self.false_label.setPos(out_false_socket.pos().x() - self.false_label.boundingRect().width() / 2,
                                    self.height - 14)

    def update_display_properties(self, config=None):
        props = dict(self.properties)
        zone_id = props.get('zone_id')
        state = props.get('state', 'N/A')
        zone_name = "НЕ ВИБРАНО"
        if config and zone_id:
            all_zones = []
            for device in config.get('devices', []):
                all_zones.extend(device.get('zones', []))

            for zone in all_zones:
                if zone['id'] == zone_id:
                    zone_name = f"{zone.get('parent_name', '')}: {zone['name']}"
                    break
        self.properties_text.setPlainText(f"{zone_name}\nСтан: {state}")

    def validate(self, config):
        props = dict(self.properties)
        zone_id = props.get('zone_id')
        if not zone_id:
            self.set_validation_state(False, "Не вибрано зону для перевірки стану.")
            return False
        if config:
            all_ids = []
            for device in config.get('devices', []):
                all_ids.extend([z['id'] for z in device.get('zones', [])])

            if zone_id not in all_ids:
                self.set_validation_state(False, f"Зону з ID '{zone_id}' не знайдено.")
                return False

        out_true_socket = self.get_socket("out_true")
        out_false_socket = self.get_socket("out_false")

        if not out_true_socket or not out_true_socket.connections:
            self.set_validation_state(False, "Вихід 'Успіх' повинен бути підключений.")
            return False
        if not out_false_socket or not out_false_socket.connections:
            self.set_validation_state(False, "Вихід 'Невдача' повинен бути підключений.")
            return False

        self.set_validation_state(True)
        return True


class SequenceNode(BaseNode):
    ICON = "→"

    def __init__(self):
        super().__init__(name="Послідовність", node_type="Композитний", color=QColor("#1e824c"), icon=self.ICON)


class DecoratorNode(BaseNode):
    def __init__(self, name, node_type, color, icon):
        super().__init__(name, node_type, color, icon)


class RepeatNode(DecoratorNode):
    ICON = "🔄"

    def __init__(self):
        super().__init__(name="Повтор", node_type="Декоратор", color=QColor("#8e44ad"), icon=self.ICON)
        self.properties.append(('count', 3))

    def _create_sockets(self):
        self.add_socket("in", position=QPointF(self.width / 2, 0))
        self.add_socket("out_loop", is_output=True, position=QPointF(self.width * 0.25, self.height))
        self.add_socket("out_end", is_output=True, position=QPointF(self.width * 0.75, self.height))

        # Labels for sockets
        out_loop_socket = self.get_socket("out_loop")
        if out_loop_socket:
            self.loop_label = QGraphicsTextItem("▶️", self)
            self.loop_label.setFont(QFont("Arial", 10))
            self.loop_label.setPos(out_loop_socket.pos().x() - self.loop_label.boundingRect().width() / 2,
                                   self.height - 18)
            self.loop_label.setToolTip("Виконати")

        out_end_socket = self.get_socket("out_end")
        if out_end_socket:
            self.end_label = QGraphicsTextItem("⏹️", self)
            self.end_label.setFont(QFont("Arial", 10))
            self.end_label.setPos(out_end_socket.pos().x() - self.end_label.boundingRect().width() / 2,
                                  self.height - 18)
            self.end_label.setToolTip("Завершити")

    def update_display_properties(self, config=None):
        props = dict(self.properties)
        count = int(props.get('count', 0))
        text = f"Виконати {count} раз" if count > 0 else "Виконувати завжди"
        self.properties_text.setPlainText(text)

    def validate(self, config):
        # First, validate the node's own properties
        props = dict(self.properties)
        try:
            count = int(props.get('count', 0))
            if count < -1:
                self.set_validation_state(False, "Кількість повторів не може бути меншою за -1.")
                return False
        except (ValueError, TypeError):
            self.set_validation_state(False, "Кількість повторів має бути числом.")
            return False

        # Then, validate its connections
        out_loop_socket = self.get_socket("out_loop")
        out_end_socket = self.get_socket("out_end")

        if not out_loop_socket or not out_loop_socket.connections:
            self.set_validation_state(False, "Вихід 'Виконати' (▶️) повинен бути підключений.")
            return False

        if not out_end_socket or not out_end_socket.connections:
            self.set_validation_state(False, "Вихід 'Завершити' (⏹️) повинен бути підключений.")
            return False

        # If all checks pass
        self.set_validation_state(True)
        return True


# --- Нові класи для Макросів ---

class MacroNode(BaseNode):
    ICON = "🧩" # Значок для макроса

    def __init__(self, macro_id=None, name="Макрос"):
        # Передаємо display name "Макрос" у super()
        super().__init__(name=name, node_type="Макрос", color=QColor("#7f8c8d"), icon=self.ICON) # Сірий колір
        self.macro_id = macro_id # ID визначення макроса в project_data['macros']
        # Сокети будуть додані динамічно пізніше

    def _create_sockets(self):
        # Поки що створимо стандартні, потім замінимо їх динамічно
        self.add_socket("in", position=QPointF(self.width / 2, 0))
        self.add_socket("out", is_output=True, position=QPointF(self.width / 2, self.height))

    # TODO: Додати update_display_properties для відображення інфо про макрос?
    # TODO: Додати validate - чи існує macro_id в проекті?


class MacroInputNode(BaseNode):
    ICON = "▶️" # Значок входу

    def __init__(self, name="Вхід"):
        # Використовуємо display name з NODE_REGISTRY для node_type
        super().__init__(name=name, node_type="Вхід Макроса", color=QColor("#1abc9c"), icon=self.ICON) # Бірюзовий
        self.height = 50 # Зробимо його меншим
        self._create_elements() # Перестворюємо елементи з новою висотою
        self._create_sockets() # Перестворюємо сокети з новою висотою

    def _create_sockets(self):
        # Тільки вихідний сокет
        self.add_socket("out", is_output=True, position=QPointF(self.width / 2, self.height))

    def _create_elements(self):
        # Спрощений вигляд
        self.rect = QGraphicsRectItem(0, 0, self.width, self.height, self)
        self.rect.setBrush(self.node_color);
        self.rect.setPen(QPen(Qt.GlobalColor.black, 1))

        self.icon_text = QGraphicsTextItem(self.node_icon, self)
        self.icon_text.setDefaultTextColor(Qt.GlobalColor.white);
        self.icon_text.setFont(QFont("Arial", 16))
        # Центрування іконки
        icon_rect = self.icon_text.boundingRect()
        self.icon_text.setPos((self.width - icon_rect.width()) / 2, (self.height - icon_rect.height()) / 2 - 5)

        self.name_text = QGraphicsTextItem(self.node_name, self)
        self.name_text.setDefaultTextColor(QColor("#f0f0f0"));
        self.name_text.setFont(QFont("Arial", 9, QFont.Weight.Bold));
        # Центрування назви під іконкою
        name_rect = self.name_text.boundingRect()
        self.name_text.setPos((self.width - name_rect.width()) / 2, self.height - name_rect.height() - 5)


class MacroOutputNode(BaseNode):
    ICON = "⏹️" # Значок виходу

    def __init__(self, name="Вихід"):
        # Використовуємо display name з NODE_REGISTRY для node_type
        super().__init__(name=name, node_type="Вихід Макроса", color=QColor("#e67e22"), icon=self.ICON) # Помаранчевий
        self.height = 50
        self._create_elements()
        self._create_sockets()

    def _create_sockets(self):
        # Тільки вхідний сокет
        self.add_socket("in", position=QPointF(self.width / 2, 0))

    def _create_elements(self):
        # Спрощений вигляд, аналогічний входу
        self.rect = QGraphicsRectItem(0, 0, self.width, self.height, self)
        self.rect.setBrush(self.node_color);
        self.rect.setPen(QPen(Qt.GlobalColor.black, 1))

        self.icon_text = QGraphicsTextItem(self.node_icon, self)
        self.icon_text.setDefaultTextColor(Qt.GlobalColor.white);
        self.icon_text.setFont(QFont("Arial", 16))
        icon_rect = self.icon_text.boundingRect()
        self.icon_text.setPos((self.width - icon_rect.width()) / 2, (self.height - icon_rect.height()) / 2 - 5)

        self.name_text = QGraphicsTextItem(self.node_name, self)
        self.name_text.setDefaultTextColor(QColor("#f0f0f0"));
        self.name_text.setFont(QFont("Arial", 9, QFont.Weight.Bold));
        name_rect = self.name_text.boundingRect()
        self.name_text.setPos((self.width - name_rect.width()) / 2, self.height - name_rect.height() - 5)


# --- Кінець нових класів ---


NODE_REGISTRY = {
    # Existing nodes (Display Name -> Class)
    "Тригер": TriggerNode,
    "Активувати вихід": ActivateOutputNode,
    "Деактивувати вихід": DeactivateOutputNode,
    "Надіслати SMS": SendSMSNode,
    "Умова: Стан зони": ConditionNodeZoneState,
    "Затримка": DelayNode,
    "Повтор": RepeatNode,
    "Послідовність": SequenceNode,
    "Макрос": MacroNode,
    # Internal nodes need display names for consistency in from_data lookups via node_type
    "Вхід Макроса": MacroInputNode,
    "Вихід Макроса": MacroOutputNode,
}

# Видалено обернений словник NODE_TYPE_TO_DISPLAY_NAME


class EditableTextItem(QGraphicsTextItem):
    """ A QGraphicsTextItem that becomes editable on double click. """

    def mouseDoubleClickEvent(self, event):
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        cursor = self.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        self.setTextCursor(cursor)
        super().mouseDoubleClickEvent(event)

    def focusOutEvent(self, event):
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        cursor = self.textCursor()
        cursor.clearSelection()
        self.setTextCursor(cursor)
        super().focusOutEvent(event)


class CommentItem(QGraphicsItem):
    def __init__(self, text="Коментар", width=200, height=100, view=None):
        super().__init__()
        self.id = generate_short_id() # Используем короткий ID
        self._width, self._height, self.view = width, height, view
        self._text = text if text is not None else "" # Ensure string
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                      QGraphicsItem.GraphicsItemFlag.ItemSendsScenePositionChanges)
        self.setZValue(-1)
        self.rect = QGraphicsRectItem(0, 0, self._width, self._height, self)
        self.rect.setBrush(QColor(255, 250, 170, 200))
        self.rect.setPen(QPen(QColor("#333333"), 1, Qt.PenStyle.DashLine))

        self.text_item = EditableTextItem(self._text, self)
        self.text_item.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.text_item.setPos(5, 5);
        self.text_item.setTextWidth(self._width - 10)

        handle_size = 10
        self.resize_handle = QGraphicsRectItem(0, 0, handle_size, handle_size, self)
        self.resize_handle.setBrush(QColor("#aaaaaa"));
        self.resize_handle.setPen(QPen(Qt.GlobalColor.black, 1))
        self.resize_handle.setPos(self._width - handle_size, self._height - handle_size)
        self.is_resizing = False
        self.start_resize_dims = None;
        self.start_mouse_pos = None

    @property
    def text(self):
        # Always get the current text from the text_item
        return self.text_item.toPlainText() if self.text_item else self._text

    @text.setter
    def text(self, value):
        self._text = value if value is not None else ""
        if self.text_item:
            self.text_item.setPlainText(self._text)

    def get_contained_nodes(self):
        contained = []
        if not self.scene(): return contained

        colliding_items = self.scene().collidingItems(self)
        for item in colliding_items:
            p = item
            while p and not isinstance(p, (BaseNode, CommentItem, FrameItem)):
                p = p.parentItem()
            if p and p not in contained:
                contained.append(p)
        return contained

    def boundingRect(self):
        return QRectF(0, 0, self._width, self._height).adjusted(-1, -1, 1, 1)

    def paint(self, painter, option, widget):
        pass

    def set_dimensions(self, width, height):
        self.prepareGeometryChange()
        self._width, self._height = max(width, 60), max(height, 40)
        self.rect.setRect(0, 0, self._width, self._height)
        self.text_item.setTextWidth(self._width - 10)
        handle_size = self.resize_handle.rect().width()
        self.resize_handle.setPos(self._width - handle_size, self._height - handle_size)

    def mousePressEvent(self, event):
        if self.resize_handle.isUnderMouse():
            self.is_resizing = True
            self.start_resize_dims = (self._width, self._height)
            self.start_mouse_pos = event.scenePos()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.is_resizing:
            delta = event.scenePos() - self.start_mouse_pos
            self.set_dimensions(self.start_resize_dims[0] + delta.x(), self.start_resize_dims[1] + delta.y())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.is_resizing:
            self.is_resizing = False
            new_dims = (self._width, self._height)
            if self.view and self.start_resize_dims and new_dims != self.start_resize_dims:
                self.view.create_resize_command(self, self.start_resize_dims, new_dims)
            self.start_resize_dims = None;
            self.start_mouse_pos = None
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedChange:
            self.rect.setPen(QPen(QColor("#fffc42"), 2, Qt.PenStyle.SolidLine) if value
                             else QPen(QColor("#333333"), 1, Qt.PenStyle.DashLine))
        return super().itemChange(change, value)

    def to_data(self):
        return {
            'id': self.id,
            'text': self.text, # Use the property to get current text
            'pos': (self.pos().x(), self.pos().y()),
            'size': (self._width, self._height)
        }

    def to_xml(self, parent_element):
        self.data_to_xml(parent_element, self.to_data())

    @staticmethod
    def data_from_xml(xml_element):
        return {'id': xml_element.get("id"),
                'text': xml_element.text or "", # Get text content
                'pos': (float(xml_element.get("x")), float(xml_element.get("y"))),
                'size': (float(xml_element.get("width")), float(xml_element.get("height")))}

    @staticmethod
    def data_to_xml(parent_element, comment_data):
        attrs = {
            'id': str(comment_data.get('id', '')),
            'x': str(comment_data.get('pos', [0, 0])[0]),
            'y': str(comment_data.get('pos', [0, 0])[1]),
            'width': str(comment_data.get('size', [0, 0])[0]),
            'height': str(comment_data.get('size', [0, 0])[1])
        }
        comment_el = ET.SubElement(parent_element, "comment", **attrs)
        # Ensure text is a string
        comment_el.text = str(comment_data.get('text', ''))

    @classmethod
    def from_data(cls, data, view):
        comment = cls(data['text'], data['size'][0], data['size'][1], view)
        comment.id = data.get('id', generate_short_id()) # Используем короткий ID
        comment.setPos(QPointF(*data['pos']))
        return comment


class FrameItem(QGraphicsItem):
    def __init__(self, text="Новая группа", width=300, height=200, view=None):
        super().__init__()
        self.id = generate_short_id() # Используем короткий ID
        self._width, self._height, self.view = width, height, view
        self._text = text if text is not None else "" # Ensure string
        self.header_height = 30
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                      QGraphicsItem.GraphicsItemFlag.ItemSendsScenePositionChanges)
        self.setZValue(-2) # Ensure frame is behind nodes
        self.rect = QGraphicsRectItem(0, 0, self._width, self._height, self)
        self.rect.setBrush(QColor(80, 80, 80, 180))
        self.rect.setPen(QPen(QColor(118, 185, 237), 1, Qt.PenStyle.DashLine))
        self.header = QGraphicsRectItem(0, 0, self._width, self.header_height, self)
        self.header.setBrush(QColor(118, 185, 237))
        self.header.setPen(QPen(Qt.PenStyle.NoPen))

        self.text_item = EditableTextItem(self._text, self)
        self.text_item.setDefaultTextColor(Qt.GlobalColor.white)
        font = QFont("Arial", 10, QFont.Weight.Bold)
        self.text_item.setFont(font)
        self.text_item.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.text_item.setPos(5, 5)
        self.text_item.setTextWidth(self._width - 10)

        handle_size = 10
        self.resize_handle = QGraphicsRectItem(0, 0, handle_size, handle_size, self)
        self.resize_handle.setBrush(QColor("#aaaaaa"))
        self.resize_handle.setPen(QPen(Qt.GlobalColor.black, 1))
        self.resize_handle.setPos(self._width - handle_size, self._height - handle_size)
        self.is_resizing = False
        self.start_resize_dims = None
        self.start_mouse_pos = None
        self._contained_start_positions = {} # Store positions for moving contained items


    @property
    def text(self):
        return self.text_item.toPlainText() if self.text_item else self._text

    @text.setter
    def text(self, value):
        self._text = value if value is not None else ""
        if self.text_item:
            self.text_item.setPlainText(self._text)

    def get_contained_nodes(self):
        """Finds items visually contained within the frame."""
        contained = []
        if not self.scene(): return contained

        # Get the frame's bounding rectangle in scene coordinates
        frame_rect = self.sceneBoundingRect()

        # Check items whose bounding box *might* intersect (optimization)
        potential_items = self.scene().items(frame_rect)

        for item in potential_items:
             # Check if the item's center is within the frame's boundaries
             # Ensure item is a BaseNode or CommentItem and not the frame itself
             if isinstance(item, (BaseNode, CommentItem)) and item is not self:
                  item_center = item.sceneBoundingRect().center()
                  # Check containment using the frame's *internal* rect (excluding potential selection outline)
                  if frame_rect.contains(item_center):
                       contained.append(item)
        return contained


    def boundingRect(self):
        # Add a small margin for selection outline if needed
        return QRectF(0, 0, self._width, self._height).adjusted(-1, -1, 1, 1)


    def paint(self, painter, option, widget):
        pass # Base class does not paint itself, children elements do


    def set_dimensions(self, width, height):
        self.prepareGeometryChange()
        self._width, self._height = max(width, 100), max(height, 60 + self.header_height) # Ensure min size allows for header
        self.rect.setRect(0, 0, self._width, self._height)
        self.header.setRect(0, 0, self._width, self.header_height)
        self.text_item.setTextWidth(self._width - 10)
        handle_size = self.resize_handle.rect().width()
        self.resize_handle.setPos(self._width - handle_size, self._height - handle_size)
        self.update() # Ensure repaint after resize


    def mousePressEvent(self, event):
        if self.resize_handle.isUnderMouse():
            self.is_resizing = True
            self.start_resize_dims = (self._width, self._height)
            self.start_mouse_pos = event.scenePos()
            event.accept()
        else:
            # Store initial positions of contained nodes before moving the frame
            self._contained_start_positions = {node: node.pos() for node in self.get_contained_nodes()}
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.is_resizing:
            delta = event.scenePos() - self.start_mouse_pos
            self.set_dimensions(self.start_resize_dims[0] + delta.x(), self.start_resize_dims[1] + delta.y())
            event.accept()
        else:
            # Handle moving contained items along with the frame
            old_pos = self.pos()
            super().mouseMoveEvent(event) # Let the base class handle the move
            new_pos = self.pos()
            delta = new_pos - old_pos
            if delta.manhattanLength() > 0:
                 # Check if nodes are selected; if so, they are moved independently
                 selected_items = self.scene().selectedItems() if self.scene() else []
                 for node, start_pos in self._contained_start_positions.items():
                     # Only move nodes not part of the current selection drag AND still inside the frame visually
                     if node not in selected_items and self.sceneBoundingRect().contains(node.sceneBoundingRect().center()):
                          node.setPos(node.pos() + delta)


    def mouseReleaseEvent(self, event):
        if self.is_resizing:
            self.is_resizing = False
            new_dims = (self._width, self._height)
            if self.view and self.start_resize_dims and new_dims != self.start_resize_dims:
                self.view.create_resize_command(self, self.start_resize_dims, new_dims)
            self.start_resize_dims = None
            self.start_mouse_pos = None
            event.accept()
        else:
            # Clear stored positions after move is complete
            self._contained_start_positions = {}
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        # Only allow editing by double-clicking the header area
        if event.pos().y() < self.header_height:
            # Activate editing on the text item
            self.text_item.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
            self.text_item.setFocus(Qt.FocusReason.MouseFocusReason)
            # Select all text for easy replacement
            cursor = self.text_item.textCursor()
            cursor.select(QTextCursor.SelectionType.Document)
            self.text_item.setTextCursor(cursor)
        else:
            # Allow entering macro on double click outside header? (Future)
            super().mouseDoubleClickEvent(event)


    def itemChange(self, change, value):
        # Handle selection highlight
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedChange:
            pen = QPen(QColor("#fffc42"), 2, Qt.PenStyle.SolidLine) if value \
                else QPen(QColor(118, 185, 237), 1, Qt.PenStyle.DashLine)
            self.rect.setPen(pen)

        return super().itemChange(change, value)


    def to_data(self):
        return {
            'id': self.id,
            'text': self.text, # Use property
            'pos': (self.pos().x(), self.pos().y()),
            'size': (self._width, self._height)
        }

    def to_xml(self, parent_element):
        self.data_to_xml(parent_element, self.to_data())

    @staticmethod
    def data_from_xml(xml_element):
        return {'id': xml_element.get("id"),
                'text': xml_element.text or "", # Get text content
                'pos': (float(xml_element.get("x")), float(xml_element.get("y"))),
                'size': (float(xml_element.get("width")), float(xml_element.get("height")))}

    @staticmethod
    def data_to_xml(parent_element, frame_data):
        attrs = {
            'id': str(frame_data.get('id', '')),
            'x': str(frame_data.get('pos', [0, 0])[0]),
            'y': str(frame_data.get('pos', [0, 0])[1]),
            'width': str(frame_data.get('size', [0, 0])[0]),
            'height': str(frame_data.get('size', [0, 0])[1])
        }
        frame_el = ET.SubElement(parent_element, "frame", **attrs)
        # Ensure text is a string
        frame_el.text = str(frame_data.get('text', ''))

    @classmethod
    def from_data(cls, data, view):
        frame = cls(data['text'], data['size'][0], data['size'][1], view)
        frame.id = data.get('id', generate_short_id()) # Используем короткий ID
        frame.setPos(QPointF(*data['pos']))
        return frame

