import uuid
from enum import Enum, auto
from lxml import etree as ET
from PyQt6.QtGui import QColor, QPen, QBrush, QFont, QPainterPath, QTextCursor
from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtWidgets import QGraphicsItem, QGraphicsRectItem, QGraphicsTextItem, QGraphicsEllipseItem, QGraphicsPathItem


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
        p1, p2 = self.start_socket.scenePos(), self.end_socket.scenePos()
        path = QPainterPath(p1)
        path.cubicTo(p1 + QPointF(0, 50), p2 - QPointF(0, 50), p2)
        self.setPath(path)

    def to_data(self):
        start_node = self.start_socket.parentItem()
        end_node = self.end_socket.parentItem()
        if start_node and end_node:
            return {
                'from_node': start_node.id,
                'from_socket': self.start_socket.socket_name,
                'to_node': end_node.id
            }
        return {}

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
        ET.SubElement(parent_element, "connection",
                      from_node=conn_data.get('from_node', ''),
                      from_socket=conn_data.get('from_socket', 'out'),
                      to_node=conn_data.get('to_node', ''))


class Socket(QGraphicsEllipseItem):
    def __init__(self, parent_node, socket_name="in", is_output=False):
        super().__init__(-6, -6, 12, 12, parent_node)
        self.is_output, self.connections = is_output, []
        self.socket_name = socket_name
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
        self.connections.append(connection)

    def remove_connection(self, connection):
        if connection in self.connections: self.connections.remove(connection)


class BaseNode(QGraphicsItem):
    def __init__(self, name="Вузол", node_type="Base", color=QColor("#4A90E2"), icon="●"):
        super().__init__()
        self.id = generate_short_id() # Використовуємо короткий ID
        self._node_name, self._description = name, ""
        self.node_type, self.node_color, self.node_icon = node_type, color, icon
        self.width, self.height = 180, 85
        self.properties = []
        self.setFlags(self.flags() | QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                      QGraphicsItem.GraphicsItemFlag.ItemSendsScenePositionChanges)
        self.setZValue(1)
        self.active_pen = QPen(QColor(93, 173, 226), 2, Qt.PenStyle.DashLine)
        self._create_elements();
        self._create_sockets()
        self._create_validation_indicator()

    @property
    def node_name(self):
        return self._node_name

    @node_name.setter
    def node_name(self, value):
        self._node_name = value;
        self.name_text.setPlainText(value)

    @property
    def description(self):
        return self._description

    @description.setter
    def description(self, value):
        self._description = value

    def _create_elements(self):
        self.rect = QGraphicsRectItem(0, 0, self.width, self.height, self)
        self.rect.setBrush(self.node_color);
        self.rect.setPen(QPen(Qt.GlobalColor.black, 1))

        self.icon_text = QGraphicsTextItem(self.node_icon, self)
        self.icon_text.setDefaultTextColor(Qt.GlobalColor.white);
        self.icon_text.setFont(QFont("Arial", 16))
        self.icon_text.setPos(8, 4)

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

    def get_socket(self, name):
        if name == "in" and hasattr(self, 'in_socket'): return self.in_socket
        if name == "out" and hasattr(self, 'out_socket'): return self.out_socket
        if name == "out_true" and hasattr(self, 'out_socket_true'): return self.out_socket_true
        if name == "out_false" and hasattr(self, 'out_socket_false'): return self.out_socket_false
        if name == "out_loop" and hasattr(self, 'out_socket_loop'): return self.out_socket_loop
        if name == "out_end" and hasattr(self, 'out_socket_end'): return self.out_socket_end
        return None

    def get_all_sockets(self):
        sockets = []
        if hasattr(self, 'in_socket'): sockets.append(self.in_socket)
        if hasattr(self, 'out_socket'): sockets.append(self.out_socket)
        if hasattr(self, 'out_socket_true'): sockets.append(self.out_socket_true)
        if hasattr(self, 'out_socket_false'): sockets.append(self.out_socket_false)
        if hasattr(self, 'out_socket_loop'): sockets.append(self.out_socket_loop)
        if hasattr(self, 'out_socket_end'): sockets.append(self.out_socket_end)
        return list(filter(None, sockets))

    def get_output_sockets(self):
        return [sock for sock in self.get_all_sockets() if sock.is_output]

    def _create_sockets(self):
        self.in_socket = None if isinstance(self, TriggerNode) else Socket(self, "in")
        if self.in_socket: self.in_socket.setPos(self.width / 2, 0)
        self.out_socket = Socket(self, "out", is_output=True);
        self.out_socket.setPos(self.width / 2, self.height)

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
                for conn in socket.connections: conn.update_path()
        return super().itemChange(change, value)

    def boundingRect(self):
        extra = 10
        return QRectF(-extra, -extra, self.width + 2 * extra, self.height + 2 * extra)

    def paint(self, painter, option, widget):
        pass

    def to_data(self):
        return {'id': self.id, 'node_type': self.__class__.__name__, 'name': self.node_name,
                'description': self.description, 'pos': (self.pos().x(), self.pos().y()),
                'properties': self.properties}

    def to_xml(self, parent_element):
        return self.data_to_xml(parent_element, self.to_data())

    @staticmethod
    def data_from_xml(xml_element):
        data = {'id': xml_element.get("id"), 'node_type': xml_element.get("type"),
                'name': xml_element.get("name"), 'pos': (float(xml_element.get("x")), float(xml_element.get("y"))),
                'properties': []}
        desc_el = xml_element.find("description")
        data['description'] = desc_el.text or "" if desc_el is not None else ""
        props_el = xml_element.find("properties")
        if props_el is not None:
            for prop_el in props_el:
                key, value = prop_el.get("key"), prop_el.get("value")
                if key == 'zones': value = value.split(',') if value else []
                data['properties'].append((key, value))
        return data

    @staticmethod
    def data_to_xml(parent_element, node_data):
        node_el = ET.SubElement(parent_element, "node", id=node_data.get('id'), type=node_data.get('node_type'),
                                name=node_data.get('name'), x=str(node_data.get('pos', [0, 0])[0]),
                                y=str(node_data.get('pos', [0, 0])[1]))
        desc_el = ET.SubElement(node_el, "description");
        desc_el.text = node_data.get('description')
        if node_data.get('properties'):
            props_el = ET.SubElement(node_el, "properties")
            for key, value in node_data['properties']:
                if isinstance(value, list): value = ",".join(value) if value else ""
                ET.SubElement(props_el, "property", key=key, value=str(value))
        return node_el

    @classmethod
    def from_data(cls, data):
        node_class_name = data.get('node_type')
        node_class = BaseNode
        if node_class_name:
            for reg_class in NODE_REGISTRY.values():
                if reg_class.__name__ == node_class_name:
                    node_class = reg_class
                    break
        node = node_class()
        node.id = data.get('id', generate_short_id()) # Використовуємо короткий ID
        node.node_name = data.get('name', '')
        node.description = data.get('description', '')
        node.setPos(QPointF(*data.get('pos', (0, 0))))
        node.properties = data.get('properties', [])
        return node


class TriggerNode(BaseNode):
    ICON = "⚡"

    def __init__(self):
        super().__init__("Тригер", "Тригер", QColor("#c0392b"), self.ICON)
        if not self.properties:
            self.properties.append(('trigger_type', 'Пожежа'))
            self.properties.append(('zones', []))

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
        super().__init__("Активувати вихід", "Дія", QColor("#27ae60"), self.ICON)
        if not self.properties:
            self.properties.append(('output_id', ''))

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
        super().__init__("Деактивувати вихід", "Дія", QColor("#e74c3c"), self.ICON)
        if not self.properties:
            self.properties.append(('output_id', ''))

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
        super().__init__("Затримка", "Дія", QColor("#2980b9"), self.ICON)
        if not self.properties:
            self.properties.append(('seconds', 5))

    def update_display_properties(self, config=None):
        props = dict(self.properties)
        seconds = props.get('seconds', 0)
        self.properties_text.setPlainText(f"{seconds} сек.")


class SendSMSNode(BaseNode):
    ICON = "✉️"

    def __init__(self):
        super().__init__("Надіслати SMS", "Дія", QColor("#9b59b6"), self.ICON)
        if not self.properties:
            self.properties.append(('user_id', ''))
            self.properties.append(('message', 'Тривога!'))

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
        super().__init__("Умова: Стан зони", "Умова", QColor("#f39c12"), self.ICON)
        if not self.properties:
            self.properties.append(('zone_id', ''))
            self.properties.append(('state', 'Під охороною'))

    def _create_sockets(self):
        self.in_socket = Socket(self, "in")
        self.in_socket.setPos(self.width / 2, 0)

        self.out_socket_true = Socket(self, "out_true", is_output=True)
        self.out_socket_true.setPos(self.width * 0.25, self.height)

        self.out_socket_false = Socket(self, "out_false", is_output=True)
        self.out_socket_false.setPos(self.width * 0.75, self.height)

        # Labels for sockets
        self.true_label = QGraphicsTextItem("Успіх", self)
        self.true_label.setDefaultTextColor(QColor("#aaffaa"))
        self.true_label.setFont(QFont("Arial", 7))
        self.true_label.setPos(self.out_socket_true.pos().x() - self.true_label.boundingRect().width() / 2,
                               self.height - 14)

        self.false_label = QGraphicsTextItem("Невдача", self)
        self.false_label.setDefaultTextColor(QColor("#ffaaaa"))
        self.false_label.setFont(QFont("Arial", 7))
        self.false_label.setPos(self.out_socket_false.pos().x() - self.false_label.boundingRect().width() / 2,
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

        if not self.out_socket_true.connections:
            self.set_validation_state(False, "Вихід 'Успіх' повинен бути підключений.")
            return False
        if not self.out_socket_false.connections:
            self.set_validation_state(False, "Вихід 'Невдача' повинен бути підключений.")
            return False

        self.set_validation_state(True)
        return True


class SequenceNode(BaseNode):
    ICON = "→"

    def __init__(self):
        super().__init__("Послідовність", "Композитний", QColor("#1e824c"), self.ICON)


class DecoratorNode(BaseNode):
    def __init__(self, name, node_type, color, icon):
        super().__init__(name, node_type, color, icon)


class RepeatNode(DecoratorNode):
    ICON = "🔄"

    def __init__(self):
        super().__init__("Повтор", "Декоратор", QColor("#8e44ad"), self.ICON)
        if not self.properties:
            self.properties.append(('count', 3))

    def _create_sockets(self):
        self.in_socket = Socket(self, "in")
        self.in_socket.setPos(self.width / 2, 0)

        self.out_socket_loop = Socket(self, "out_loop", is_output=True)
        self.out_socket_loop.setPos(self.width * 0.25, self.height)

        self.out_socket_end = Socket(self, "out_end", is_output=True)
        self.out_socket_end.setPos(self.width * 0.75, self.height)

        # Labels for sockets
        self.loop_label = QGraphicsTextItem("▶️", self)
        self.loop_label.setFont(QFont("Arial", 10))
        self.loop_label.setPos(self.out_socket_loop.pos().x() - self.loop_label.boundingRect().width() / 2,
                               self.height - 18)
        self.loop_label.setToolTip("Виконати")

        self.end_label = QGraphicsTextItem("⏹️", self)
        self.end_label.setFont(QFont("Arial", 10))
        self.end_label.setPos(self.out_socket_end.pos().x() - self.end_label.boundingRect().width() / 2,
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
        if not self.out_socket_loop.connections:
            self.set_validation_state(False, "Вихід 'Виконати' (▶️) повинен бути підключений.")
            return False

        if not self.out_socket_end.connections:
            self.set_validation_state(False, "Вихід 'Завершити' (⏹️) повинен бути підключений.")
            return False

        # If all checks pass
        self.set_validation_state(True)
        return True


NODE_REGISTRY = {
    "Тригер": TriggerNode,
    "Активувати вихід": ActivateOutputNode,
    "Деактивувати вихід": DeactivateOutputNode,
    "Надіслати SMS": SendSMSNode,
    "Умова: Стан зони": ConditionNodeZoneState,
    "Затримка": DelayNode,
    "Повтор": RepeatNode,
    "Послідовність": SequenceNode,
}


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
        self.id = generate_short_id() # Використовуємо короткий ID
        self._width, self._height, self.view = width, height, view
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                      QGraphicsItem.GraphicsItemFlag.ItemSendsScenePositionChanges)
        self.setZValue(-1)
        self.rect = QGraphicsRectItem(0, 0, self._width, self._height, self)
        self.rect.setBrush(QColor(255, 250, 170, 200))
        self.rect.setPen(QPen(QColor("#333333"), 1, Qt.PenStyle.DashLine))

        self.text_item = EditableTextItem(text, self)
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
        return QRectF(0, 0, self._width, self._height)

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
            'text': self.text_item.toPlainText(),
            'pos': (self.pos().x(), self.pos().y()),
            'size': (self._width, self._height)
        }

    def to_xml(self, parent_element):
        self.data_to_xml(parent_element, self.to_data())

    @staticmethod
    def data_from_xml(xml_element):
        return {'id': xml_element.get("id"), 'text': xml_element.text or "",
                'pos': (float(xml_element.get("x")), float(xml_element.get("y"))),
                'size': (float(xml_element.get("width")), float(xml_element.get("height")))}

    @staticmethod
    def data_to_xml(parent_element, comment_data):
        comment_el = ET.SubElement(parent_element, "comment", id=comment_data.get('id'),
                                   x=str(comment_data.get('pos', [0, 0])[0]), y=str(comment_data.get('pos', [0, 0])[1]),
                                   width=str(comment_data.get('size', [0, 0])[0]),
                                   height=str(comment_data.get('size', [0, 0])[1]))
        comment_el.text = comment_data.get('text')

    @classmethod
    def from_data(cls, data, view):
        comment = cls(data['text'], data['size'][0], data['size'][1], view)
        comment.id = data.get('id', generate_short_id()) # Використовуємо короткий ID
        comment.setPos(QPointF(*data['pos']))
        return comment


class FrameItem(QGraphicsItem):
    def __init__(self, text="Новая группа", width=300, height=200, view=None):
        super().__init__()
        self.id = generate_short_id() # Використовуємо короткий ID
        self._width, self._height, self.view = width, height, view
        self.header_height = 30
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                      QGraphicsItem.GraphicsItemFlag.ItemSendsScenePositionChanges)
        self.setZValue(-2)
        self.rect = QGraphicsRectItem(0, 0, self._width, self._height, self)
        self.rect.setBrush(QColor(80, 80, 80, 180))
        self.rect.setPen(QPen(QColor(118, 185, 237), 1, Qt.PenStyle.DashLine))
        self.header = QGraphicsRectItem(0, 0, self._width, self.header_height, self)
        self.header.setBrush(QColor(118, 185, 237))
        self.header.setPen(QPen(Qt.PenStyle.NoPen))

        self.text_item = EditableTextItem(text, self)
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
        self._old_pos = None

    def get_contained_nodes(self):
        contained = []
        if not self.scene(): return contained

        colliding_items = self.scene().collidingItems(self)
        for item in colliding_items:
            # Get the top-level item (node, comment, etc.)
            p = item
            while p and not isinstance(p, (BaseNode, CommentItem)):
                p = p.parentItem()

            # Add to list if it's a valid, new item
            if p and p not in contained:
                contained.append(p)
        return contained

    def boundingRect(self):
        return QRectF(0, 0, self._width, self._height)

    def paint(self, painter, option, widget):
        pass

    def set_dimensions(self, width, height):
        self.prepareGeometryChange()
        self._width, self._height = max(width, 100), max(height, 60)
        self.rect.setRect(0, 0, self._width, self._height)
        self.header.setRect(0, 0, self._width, self.header_height)
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
            self.start_resize_dims = None
            self.start_mouse_pos = None
            event.accept()
        else:
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
            super().mouseDoubleClickEvent(event)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedChange:
            pen = QPen(QColor("#fffc42"), 2, Qt.PenStyle.SolidLine) if value \
                else QPen(QColor(118, 185, 237), 1, Qt.PenStyle.DashLine)
            self.rect.setPen(pen)

        if self.scene():
            if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
                self._old_pos = self.pos()

            if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
                if self._old_pos is not None:
                    new_pos = self.pos()
                    delta = new_pos - self._old_pos
                    if delta.manhattanLength() > 0:
                        for child in self.get_contained_nodes():
                            if not child.isSelected():
                                child.setPos(child.pos() + delta)
                    self._old_pos = None

        return super().itemChange(change, value)

    def to_data(self):
        return {
            'id': self.id,
            'text': self.text_item.toPlainText(),
            'pos': (self.pos().x(), self.pos().y()),
            'size': (self._width, self._height)
        }

    def to_xml(self, parent_element):
        self.data_to_xml(parent_element, self.to_data())

    @staticmethod
    def data_from_xml(xml_element):
        return {'id': xml_element.get("id"), 'text': xml_element.text or "",
                'pos': (float(xml_element.get("x")), float(xml_element.get("y"))),
                'size': (float(xml_element.get("width")), float(xml_element.get("height")))}

    @staticmethod
    def data_to_xml(parent_element, frame_data):
        frame_el = ET.SubElement(parent_element, "frame", id=frame_data.get('id'),
                                 x=str(frame_data.get('pos', [0, 0])[0]), y=str(frame_data.get('pos', [0, 0])[1]),
                                 width=str(frame_data.get('size', [0, 0])[0]),
                                 height=str(frame_data.get('size', [0, 0])[1]))
        frame_el.text = frame_data.get('text')

    @classmethod
    def from_data(cls, data, view):
        frame = cls(data['text'], data['size'][0], data['size'][1], view)
        frame.id = data.get('id', generate_short_id()) # Використовуємо короткий ID
        frame.setPos(QPointF(*data['pos']))
        return frame