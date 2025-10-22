import uuid
import logging # Додано для логування
from copy import deepcopy # <-- ДОДАНО ІМПОРТ
from enum import Enum, auto
from lxml import etree as ET
from PyQt6.QtGui import QColor, QPen, QBrush, QFont, QPainterPath, QTextCursor, QTextOption # Додано QTextOption
from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtWidgets import QGraphicsItem, QGraphicsRectItem, QGraphicsTextItem, QGraphicsEllipseItem, QGraphicsPathItem, QInputDialog # Додано QInputDialog

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
        self.setZValue(0) # З'єднання мають бути під вузлами
        if self.start_socket: self.start_socket.add_connection(self)
        if self.end_socket: self.end_socket.add_connection(self)
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.update_path()

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedChange:
            self.setPen(self.selected_pen if value else self.default_pen)
            # Піднімаємо вибране з'єднання вище для кращої видимості
            self.setZValue(1 if value else 0)
        return super().itemChange(change, value)

    def set_active_state(self, active):
        if active:
            self.setPen(self.active_pen)
            self.setZValue(2) # Активні з'єднання найвище
        else:
            is_selected = self.isSelected()
            self.setPen(self.selected_pen if is_selected else self.default_pen)
            self.setZValue(1 if is_selected else 0) # Повертаємо Z-індекс

    def update_path(self):
        # Перевірка чи сокети ще існують і прив'язані до сцени
        if not self.start_socket or not self.end_socket or \
           not self.start_socket.scene() or not self.end_socket.scene() or \
           not self.start_socket.parentItem() or not self.end_socket.parentItem() or \
           not self.start_socket.parentItem().scene() or not self.end_socket.parentItem().scene():
            # log.warning(f"Connection.update_path(): Invalid sockets or parent nodes found for connection between {self.start_socket.parentItem().id if self.start_socket and self.start_socket.parentItem() else '?'} and {self.end_socket.parentItem().id if self.end_socket and self.end_socket.parentItem() else '?'}. Removing connection.")
            # Якщо сокети або їх батьки недійсні, видаляємо з'єднання
            if self.scene():
                # Обережно видаляємо посилання перед видаленням зі сцени
                if self.start_socket: self.start_socket.remove_connection(self)
                if self.end_socket: self.end_socket.remove_connection(self)
                try: # Додаємо try-except навколо removeItem
                    self.scene().removeItem(self)
                except Exception as e:
                    log.error(f"Error removing connection during update_path: {e}", exc_info=True)
            return

        p1, p2 = self.start_socket.scenePos(), self.end_socket.scenePos()
        path = QPainterPath(p1)

        try: # Додаємо try-except навколо розрахунку кривої та setPath
            # --- Повертаємо логіку кривої Безьє ---
            dx = p2.x() - p1.x()
            dy = p2.y() - p1.y()

            # Стандартні контрольні точки (вертикальні)
            # Збільшуємо базовий вигин та залежність від dy
            ctrl1 = p1 + QPointF(0, max(60, abs(dy * 0.6)))
            ctrl2 = p2 - QPointF(0, max(60, abs(dy * 0.6)))

            # Якщо вузли дуже близько по вертикалі або перекриваються горизонтально,
            # робимо S-подібну криву
            vertical_threshold = 40 # Збільшуємо поріг
            # Використовуємо ширину батьківських елементів для розрахунку перекриття
            start_node_width = self.start_socket.parentItem().width if self.start_socket.parentItem() else 180
            end_node_width = self.end_socket.parentItem().width if self.end_socket.parentItem() else 180
            # Поріг - половина суми ширин (приблизно)
            horizontal_overlap_threshold = (start_node_width + end_node_width) / 2

            # Додаткова умова: якщо кінцевий вузол значно вище початкового
            is_end_node_higher = dy < -vertical_threshold * 2 # Наприклад, вдвічі більше порогу

            if abs(dy) < vertical_threshold or abs(dx) < horizontal_overlap_threshold or is_end_node_higher:
                 # Використовуємо половину горизонтальної відстані, але не менше певного значення
                 offset_x = max(80, abs(dx) * 0.5) # Збільшуємо мінімальний горизонтальний вигин
                 # Змінюємо вертикальний вигин залежно від dy
                 offset_y = 70 if abs(dy) < vertical_threshold else 40 # Менший вигин, якщо вузли далеко по вертикалі

                 # Якщо кінцевий вузол значно вище, робимо вигин "через верх"
                 if is_end_node_higher:
                      offset_y = -abs(offset_y) # Інвертуємо вертикальний зсув

                 ctrl1 = p1 + QPointF(offset_x if dx > 0 else -offset_x, offset_y)
                 ctrl2 = p2 - QPointF(offset_x if dx > 0 else -offset_x, offset_y)


            path.cubicTo(ctrl1, ctrl2, p2)
            # --- Кінець логіки кривої Безьє ---

            self.setPath(path)

        except Exception as e:
             log.error(f"Error calculating or setting connection path: {e}", exc_info=True)
             # Якщо сталася помилка, малюємо просту пряму лінію як запасний варіант
             path = QPainterPath(p1)
             path.lineTo(p2)
             try:
                  self.setPath(path)
             except Exception as e2:
                  log.error(f"Failed to set fallback line path: {e2}", exc_info=True)


    def to_data(self):
        start_node = self.start_socket.parentItem() if self.start_socket else None
        end_node = self.end_socket.parentItem() if self.end_socket else None
        # Check if parent nodes exist AND sockets are valid before saving
        if start_node and end_node and self.start_socket and self.end_socket:
            # Check if parent nodes still exist (important for undo/redo)
            if start_node.scene() and end_node.scene():
                return {
                    'from_node': start_node.id,
                    'from_socket': self.start_socket.socket_name,
                    'to_node': end_node.id,
                    'to_socket': self.end_socket.socket_name # Зберігаємо ім'я цільового сокета
                }
        # log.warning(f"Connection.to_data(): Invalid connection detected (start={start_node}, end={end_node})")
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
            'to_node': xml_element.get("to_node"),
            'to_socket': xml_element.get("to_socket", "in") # Додано читання цільового сокета
        }

    @classmethod
    def from_data(cls, data):
        # This method seems unused, Connection is created directly
        log.warning("Connection.from_data() called, but it's likely unused.")
        return data

    @staticmethod
    def data_to_xml(parent_element, conn_data):
        # Ensure all values are strings, provide defaults
        attrs = {
            "from_node": str(conn_data.get('from_node', '')),
            "from_socket": str(conn_data.get('from_socket', 'out')),
            "to_node": str(conn_data.get('to_node', '')),
            "to_socket": str(conn_data.get('to_socket', 'in')) # Додано збереження цільового сокета
        }
        ET.SubElement(parent_element, "connection", **attrs)


class Socket(QGraphicsEllipseItem):
    def __init__(self, parent_node, socket_name="in", is_output=False, display_name=None):
        super().__init__(-6, -6, 12, 12, parent_node)
        self.parent_node = parent_node # Зберігаємо посилання на батьківський вузол
        self.is_output, self.connections = is_output, []
        self.socket_name = socket_name # e.g., "in", "out", "out_true", "macro_in_1", "macro_out_exec"
        self.display_name = display_name if display_name else socket_name # Ім'я для відображення (ToolTip)
        self.default_brush = QBrush(QColor("#d4d4d4"))
        self.hover_brush = QBrush(QColor("#77dd77"))
        self.is_highlighted = False
        self.setBrush(self.default_brush);
        self.setPen(QPen(QColor("#3f3f3f"), 2))
        self.setAcceptHoverEvents(True)
        self.setZValue(2)
        self.setToolTip(self.display_name) # Показуємо ім'я сокета при наведенні

    def hoverEnterEvent(self, event):
        view = self.scene().views()[0] if self.scene().views() else None
        # Підсвічуємо тільки якщо не тягнемо лінію АБО якщо це валідний сокет для завершення
        if view and (not view.start_socket or self.is_highlighted):
            self.setBrush(self.hover_brush)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        if not self.is_highlighted: # Залишаємо підсвіченим, якщо це валідна ціль
            self.setBrush(self.default_brush)
        super().hoverLeaveEvent(event)

    def set_highlight(self, highlight):
        if self.is_highlighted == highlight:
            return
        self.is_highlighted = highlight
        # Встановлюємо кисть відповідно до підсвітки АБО стану hover
        is_hovered = self.isUnderMouse()
        if highlight:
            self.setBrush(self.hover_brush)
        elif is_hovered:
            self.setBrush(self.hover_brush) # Залишаємо hover, якщо не валідна ціль, але миша над ним
        else:
            self.setBrush(self.default_brush)
        self.update()


    def add_connection(self, connection):
        if connection not in self.connections:
            self.connections.append(connection)

    def remove_connection(self, connection):
        if connection in self.connections:
            self.connections.remove(connection)
        else:
            # log.warning(f"Attempted to remove non-existent connection from socket {self.socket_name} on node {self.parent_node.id if self.parent_node else '?'}")
            pass # Не логуємо, це може бути нормальним при komplexних undo/redo


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
        self.width, self.height = 180, 85 # Default size
        self.properties = []
        self._sockets = {} # Dictionary to store sockets {socket_name: Socket}
        self.setFlags(self.flags() | QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                      QGraphicsItem.GraphicsItemFlag.ItemSendsScenePositionChanges)
        self.setZValue(1) # Вузли над з'єднаннями (Z=0)
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
        # Прямокутник вузла
        self.rect = QGraphicsRectItem(0, 0, self.width, self.height, self)
        self.rect.setBrush(self.node_color);
        self.rect.setPen(QPen(Qt.GlobalColor.black, 1))

        # Іконка
        self.icon_text = QGraphicsTextItem(self.node_icon, self)
        self.icon_text.setDefaultTextColor(Qt.GlobalColor.white);
        self.icon_text.setFont(QFont("Arial", 16))
        self.icon_text.setPos(8, 4)

        # Тип вузла (використовуємо display name)
        self.type_text = QGraphicsTextItem(self.node_type, self)
        self.type_text.setDefaultTextColor(Qt.GlobalColor.white);
        self.type_text.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        self.type_text.setPos(40, 8)

        # Ім'я вузла
        self.name_text = QGraphicsTextItem(self.node_name, self)
        self.name_text.setDefaultTextColor(QColor("#f0f0f0"));
        self.name_text.setFont(QFont("Arial", 9, QFont.Weight.Bold));
        self.name_text.setPos(8, 35)
        # Обмеження ширини тексту імені
        self.name_text.setTextWidth(self.width - 16)

        # Текст властивостей
        self.properties_text = QGraphicsTextItem("", self)
        self.properties_text.setDefaultTextColor(QColor("#cccccc"));
        self.properties_text.setFont(QFont("Arial", 8))
        self.properties_text.setPos(8, 55)
        # Обмеження ширини тексту властивостей
        self.properties_text.setTextWidth(self.width - 16)


    def add_socket(self, name, is_output=False, position=None, display_name=None):
        """Adds a socket to the node."""
        if name in self._sockets:
            # log.warning(f"Socket '{name}' already exists on node {self.id}. Returning existing.")
            return self._sockets[name]
        socket = Socket(self, socket_name=name, is_output=is_output, display_name=display_name)
        if position:
            socket.setPos(position)
        else: # Автоматичне розміщення, якщо позиція не вказана (дуже базове)
             count = len(self.get_input_sockets() if not is_output else self.get_output_sockets())
             spacing = 30
             y_pos = 0 if not is_output else self.height
             x_pos = (self.width / 2) + (count - 0.5 * (len(self._sockets) -1) ) * spacing # Приблизно центруємо
             socket.setPos(x_pos, y_pos)

        self._sockets[name] = socket
        return socket

    def remove_socket(self, name):
         """Removes a socket and disconnects its connections."""
         socket = self._sockets.pop(name, None)
         if socket:
              # Від'єднуємо всі з'єднання від цього сокету
              for conn in list(socket.connections): # Копіюємо список перед ітерацією
                   # Видаляємо посилання на з'єднання з іншого сокету
                   other_socket = conn.start_socket if conn.end_socket == socket else conn.end_socket
                   if other_socket:
                        other_socket.remove_connection(conn)
                   # Видаляємо саме з'єднання зі сцени
                   if conn.scene():
                        conn.scene().removeItem(conn)
              # Видаляємо сокет зі сцени
              if socket.scene():
                   socket.scene().removeItem(socket)
              log.debug(f"Removed socket '{name}' from node {self.id}")

    def clear_sockets(self):
         """Removes all sockets from the node."""
         log.debug(f"Clearing all sockets from node {self.id}")
         for name in list(self._sockets.keys()): # Копіюємо ключі
              self.remove_socket(name)
         self._sockets = {}


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
        # Nodes that shouldn't have an input socket by default
        no_input_nodes = (TriggerNode, MacroInputNode)
        # Nodes that shouldn't have an output socket by default
        no_output_nodes = (ActivateOutputNode, DeactivateOutputNode, SendSMSNode, MacroOutputNode)

        log.debug(f"_create_sockets running for {type(self).__name__} {self.id}")
        if not isinstance(self, no_input_nodes):
             self.add_socket("in", is_output=False, position=QPointF(self.width / 2, 0), display_name="Вхід")
        if not isinstance(self, no_output_nodes):
            self.add_socket("out", is_output=True, position=QPointF(self.width / 2, self.height), display_name="Вихід")


    def _create_validation_indicator(self):
        self.error_icon = QGraphicsTextItem("⚠️", self)
        self.error_icon.setFont(QFont("Arial", 12))
        self.error_icon.setPos(self.width - 24, 2)
        self.error_icon.setZValue(3) # Над іншими елементами вузла
        self.error_icon.setVisible(False)

    def set_validation_state(self, is_valid, message=""):
        self.error_icon.setVisible(not is_valid)
        self.error_icon.setToolTip(message if not is_valid else "") # Повідомлення тільки для помилки

    def set_active_state(self, active):
        if active:
            self.rect.setPen(self.active_pen)
        else:
            is_selected = self.isSelected()
            self.rect.setPen(QPen(QColor("#fffc42"), 2) if is_selected else QPen(Qt.GlobalColor.black, 1))
        # Оновлюємо ZValue, щоб активний/вибраний вузол був вище
        self.setZValue(3 if active else 2 if self.isSelected() else 1)


    def validate(self, config):
        # Базова валідація - просто скидаємо помилку
        self.set_validation_state(True)
        return True

    def update_display_properties(self, config=None):
        # Базовий метод - очищаємо текст властивостей
        self.properties_text.setPlainText("")

    def itemChange(self, change, value):
        # Оновлення Z-індексу при виборі/скасуванні вибору
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedChange:
            is_selected = value
            self.rect.setPen(QPen(QColor("#fffc42"), 2) if is_selected else QPen(Qt.GlobalColor.black, 1))
            # Вибраний вузол вище не вибраних, але нижче активних
            self.setZValue(2 if is_selected else 1)

        # Оновлення шляхів з'єднань при переміщенні
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged and self.scene():
            # Використовуємо self._sockets для надійності
            for socket in self._sockets.values():
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
        # Переконаємось, що ширина відповідає поточній ширині вузла
        rect_width = self.width if hasattr(self, 'width') else 180
        return QRectF(-extra, -extra, rect_width + 2 * extra, rect_height + 2 * extra)

    def paint(self, painter, option, widget):
        pass # Base class does not paint itself, children elements do

    def to_data(self):
        # Зберігаємо ім'я класу
        class_name = self.__class__.__name__
        data = {'id': self.id, 'node_type': class_name, 'name': self.node_name,
                'description': self.description, 'pos': (self.pos().x(), self.pos().y()),
                'properties': deepcopy(self.properties)} # Копіюємо властивості
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
        # Handle missing or empty description safely
        data['description'] = desc_el.text if desc_el is not None and desc_el.text is not None else ""
        props_el = xml_element.find("properties")
        if props_el is not None:
            for prop_el in props_el:
                key, value_str = prop_el.get("key"), prop_el.get("value")
                value = value_str # Default to string
                if key == 'zones':
                    # Ensure list even if empty, handle potential None for value_str
                    value = value_str.split(',') if value_str else []
                elif key in ('seconds', 'count'):
                    try:
                        # Handle potential None for value_str
                        value = int(value_str) if value_str is not None else 0
                    except (ValueError, TypeError):
                         log.warning(f"Could not convert property '{key}' value '{value_str}' to int for node {data.get('id')}. Using default 0.")
                         value = 0 # Default to 0 on error
                # Handle None key or value gracefully
                if key is not None:
                     data['properties'].append((key, value))
                else:
                     log.warning(f"Found property with None key for node {data.get('id')}. Skipping.")

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
        if properties and isinstance(properties, (list, tuple)): # Allow tuples too
            props_el = ET.SubElement(node_el, "properties")
            for prop_item in properties:
                 # Check if prop_item is a tuple/list of size 2
                 if isinstance(prop_item, (list, tuple)) and len(prop_item) == 2:
                      key, value = prop_item
                      # Ensure key and value are not None before converting to string
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
            found = False
            for registry_class in NODE_REGISTRY.values():
                if registry_class.__name__ == node_class_name:
                    node_class = registry_class
                    found = True
                    break
            if not found: # If loop finished without break
                 log.warning(f"Node class '{node_class_name}' not found in NODE_REGISTRY. Using BaseNode.")

        # Create instance
        try:
             # Pass only relevant args if constructor expects them (unlikely here)
             node = node_class() # Call constructor specific to the class
        except Exception as e:
             log.error(f"Error instantiating node class '{node_class_name}': {e}. Falling back to BaseNode.", exc_info=True)
             node = BaseNode() # Fallback safely

        # Set common attributes
        node.id = data.get('id', generate_short_id())
        # Use setters to ensure validation/updates if they exist
        node.node_name = data.get('name', node_class.__name__) # Use class name as fallback name
        node.description = data.get('description', '')
        node.setPos(QPointF(*data.get('pos', (0, 0))))
        # Ensure properties is a list, deepcopy might be safer if needed later
        loaded_properties = data.get('properties', [])
        node.properties = list(loaded_properties) if isinstance(loaded_properties, (list, tuple)) else []

        # Set specific attributes (like macro_id)
        if isinstance(node, MacroNode):
            node.macro_id = data.get('macro_id')
            # Dynamic sockets for MacroNode should be updated later
            # when the full project data (including macro definitions) is available.
            # We cannot do it here reliably.

        return node


class TriggerNode(BaseNode):
    ICON = "⚡"

    def __init__(self):
        # Pass the display name to super()
        super().__init__(name="Тригер", node_type="Тригер", color=QColor("#c0392b"), icon=self.ICON)
        # Initialize properties *after* super().__init__ has run
        # Use setdefault pattern or check if properties are already set (e.g., during loading)
        prop_dict = dict(self.properties)
        if 'trigger_type' not in prop_dict: self.properties.append(('trigger_type', 'Пожежа'))
        if 'zones' not in prop_dict: self.properties.append(('zones', []))


    def _create_sockets(self):
        # Override: Trigger only has an output
        self.add_socket("out", is_output=True, position=QPointF(self.width / 2, self.height), display_name="Вихід")

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
            all_zone_ids = set() # Use set for faster lookup
            for device in config.get('devices', []):
                all_zone_ids.update(z['id'] for z in device.get('zones', []))

            missing_zones = [zid for zid in zones_ids_to_check if zid not in all_zone_ids]
            if missing_zones:
                 self.set_validation_state(False, f"Зони не знайдено: {', '.join(missing_zones)}")
                 return False

        self.set_validation_state(True)
        return True


class ActivateOutputNode(BaseNode):
    ICON = "🔊"

    def __init__(self):
        super().__init__(name="Активувати вихід", node_type="Дія", color=QColor("#27ae60"), icon=self.ICON)
        if not any(p[0] == 'output_id' for p in self.properties):
             self.properties.append(('output_id', ''))

    def _create_sockets(self):
        # Override: Action nodes only have an input
        self.add_socket("in", position=QPointF(self.width / 2, 0), display_name="Вхід")

    def update_display_properties(self, config=None):
        props = dict(self.properties)
        output_id = props.get('output_id')
        output_name = "НЕ ВИБРАНО"
        if config and output_id:
            all_outputs = {} # Use dict for faster lookup {id: {'name':..., 'parent_name':...}}
            for device in config.get('devices', []):
                for out in device.get('outputs', []):
                     all_outputs[out['id']] = {'name': out['name'], 'parent_name': out.get('parent_name', '')}

            output_info = all_outputs.get(output_id)
            if output_info:
                output_name = f"{output_info.get('parent_name', '')}: {output_info['name']}"

        self.properties_text.setPlainText(output_name)

    def validate(self, config):
        props = dict(self.properties)
        output_id = props.get('output_id')
        if not output_id:
            self.set_validation_state(False, "Не вибрано вихід для активації.")
            return False
        if config:
            all_ids = set()
            for device in config.get('devices', []):
                all_ids.update(o['id'] for o in device.get('outputs', []))

            if output_id not in all_ids:
                self.set_validation_state(False, f"Вихід з ID '{output_id}' не знайдено.")
                return False

        self.set_validation_state(True)
        return True


class DeactivateOutputNode(BaseNode):
    ICON = "🔇"

    def __init__(self):
        super().__init__(name="Деактивувати вихід", node_type="Дія", color=QColor("#e74c3c"), icon=self.ICON)
        if not any(p[0] == 'output_id' for p in self.properties):
             self.properties.append(('output_id', ''))

    def _create_sockets(self):
        # Override: Action nodes only have an input
        self.add_socket("in", position=QPointF(self.width / 2, 0), display_name="Вхід")

    def update_display_properties(self, config=None):
        # Logic is identical to ActivateOutputNode
        props = dict(self.properties)
        output_id = props.get('output_id')
        output_name = "НЕ ВИБРАНО"
        if config and output_id:
            all_outputs = {}
            for device in config.get('devices', []):
                 for out in device.get('outputs', []):
                      all_outputs[out['id']] = {'name': out['name'], 'parent_name': out.get('parent_name', '')}
            output_info = all_outputs.get(output_id)
            if output_info: output_name = f"{output_info.get('parent_name', '')}: {output_info['name']}"
        self.properties_text.setPlainText(output_name)


    def validate(self, config):
        # Logic is identical to ActivateOutputNode
        props = dict(self.properties)
        output_id = props.get('output_id')
        if not output_id:
            self.set_validation_state(False, "Не вибрано вихід для деактивації.")
            return False
        if config:
            all_ids = set()
            for device in config.get('devices', []):
                all_ids.update(o['id'] for o in device.get('outputs', []))
            if output_id not in all_ids:
                self.set_validation_state(False, f"Вихід з ID '{output_id}' не знайдено.")
                return False
        self.set_validation_state(True)
        return True


class DelayNode(BaseNode):
    ICON = "⏳"

    def __init__(self):
        super().__init__(name="Затримка", node_type="Дія", color=QColor("#2980b9"), icon=self.ICON)
        if not any(p[0] == 'seconds' for p in self.properties):
            self.properties.append(('seconds', 5))

    # _create_sockets uses default BaseNode implementation (in, out)

    def update_display_properties(self, config=None):
        props = dict(self.properties)
        seconds = props.get('seconds', 0)
        self.properties_text.setPlainText(f"{seconds} сек.")


class SendSMSNode(BaseNode):
    ICON = "✉️"

    def __init__(self):
        super().__init__(name="Надіслати SMS", node_type="Дія", color=QColor("#9b59b6"), icon=self.ICON)
        prop_dict = dict(self.properties)
        if 'user_id' not in prop_dict: self.properties.append(('user_id', ''))
        if 'message' not in prop_dict: self.properties.append(('message', 'Тривога!'))

    def _create_sockets(self):
        # Override: Action nodes only have an input
        self.add_socket("in", position=QPointF(self.width / 2, 0), display_name="Вхід")

    def update_display_properties(self, config=None):
        props = dict(self.properties)
        user_id = props.get('user_id')
        user_name = "НЕ ВИБРАНО"
        if config and user_id:
            users_map = {user['id']: user['name'] for user in config.get('users', [])}
            user_name = users_map.get(user_id, "НЕ ЗНАЙДЕНО")
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
            all_ids = {user['id'] for user in config.get('users', [])}
            if user_id not in all_ids:
                self.set_validation_state(False, f"Користувача з ID '{user_id}' не знайдено.")
                return False

        self.set_validation_state(True)
        return True


class ConditionNodeZoneState(BaseNode):
    ICON = "🔎"

    def __init__(self):
        super().__init__(name="Умова: Стан зони", node_type="Умова", color=QColor("#f39c12"), icon=self.ICON)
        prop_dict = dict(self.properties)
        if 'zone_id' not in prop_dict: self.properties.append(('zone_id', ''))
        if 'state' not in prop_dict: self.properties.append(('state', 'Під охороною'))


    def _create_sockets(self):
        self.add_socket("in", position=QPointF(self.width / 2, 0), display_name="Вхід")
        self.add_socket("out_true", is_output=True, position=QPointF(self.width * 0.25, self.height), display_name="Успіх")
        self.add_socket("out_false", is_output=True, position=QPointF(self.width * 0.75, self.height), display_name="Невдача")

        # Labels for sockets (created after sockets)
        out_true_socket = self.get_socket("out_true")
        if out_true_socket:
            self.true_label = QGraphicsTextItem("✔", self) # Більш лаконічно
            self.true_label.setDefaultTextColor(QColor("#aaffaa"))
            font = QFont("Arial", 10, QFont.Weight.Bold)
            self.true_label.setFont(font)
            self.true_label.setPos(out_true_socket.pos().x() - self.true_label.boundingRect().width() / 2,
                                   self.height - 18)
            self.true_label.setToolTip("Успіх (умова виконана)")

        out_false_socket = self.get_socket("out_false")
        if out_false_socket:
            self.false_label = QGraphicsTextItem("✘", self) # Більш лаконічно
            self.false_label.setDefaultTextColor(QColor("#ffaaaa"))
            font = QFont("Arial", 10, QFont.Weight.Bold)
            self.false_label.setFont(font)
            self.false_label.setPos(out_false_socket.pos().x() - self.false_label.boundingRect().width() / 2,
                                    self.height - 18)
            self.false_label.setToolTip("Невдача (умова не виконана)")

    def update_display_properties(self, config=None):
        props = dict(self.properties)
        zone_id = props.get('zone_id')
        state = props.get('state', 'N/A')
        zone_name = "НЕ ВИБРАНО"
        if config and zone_id:
            all_zones = {}
            for device in config.get('devices', []):
                 for zone in device.get('zones', []):
                      all_zones[zone['id']] = {'name': zone['name'], 'parent_name': zone.get('parent_name', '')}
            zone_info = all_zones.get(zone_id)
            if zone_info: zone_name = f"{zone_info.get('parent_name', '')}: {zone_info['name']}"
            else: zone_name = "НЕ ЗНАЙДЕНО"

        self.properties_text.setPlainText(f"{zone_name}\nСтан: {state}")

    def validate(self, config):
        props = dict(self.properties)
        zone_id = props.get('zone_id')
        if not zone_id:
            self.set_validation_state(False, "Не вибрано зону для перевірки стану.")
            return False
        if config:
            all_ids = set()
            for device in config.get('devices', []):
                all_ids.update(z['id'] for z in device.get('zones', []))

            if zone_id not in all_ids:
                self.set_validation_state(False, f"Зону з ID '{zone_id}' не знайдено.")
                return False

        out_true_socket = self.get_socket("out_true")
        out_false_socket = self.get_socket("out_false")

        # Перевіряємо наявність сокетів перед доступом до connections
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
    # _create_sockets uses default BaseNode implementation (in, out)


class DecoratorNode(BaseNode):
    # Abstract base class for decorators like Repeat
    def __init__(self, name, node_type, color, icon):
        super().__init__(name, node_type, color, icon)


class RepeatNode(DecoratorNode):
    ICON = "🔄"

    def __init__(self):
        super().__init__(name="Повтор", node_type="Декоратор", color=QColor("#8e44ad"), icon=self.ICON)
        if not any(p[0] == 'count' for p in self.properties):
             self.properties.append(('count', 3))

    def _create_sockets(self):
        self.add_socket("in", position=QPointF(self.width / 2, 0), display_name="Вхід")
        self.add_socket("out_loop", is_output=True, position=QPointF(self.width * 0.25, self.height), display_name="Виконати")
        self.add_socket("out_end", is_output=True, position=QPointF(self.width * 0.75, self.height), display_name="Завершити")

        # Labels for sockets
        out_loop_socket = self.get_socket("out_loop")
        if out_loop_socket:
            self.loop_label = QGraphicsTextItem("▶️", self)
            self.loop_label.setFont(QFont("Arial", 10))
            self.loop_label.setPos(out_loop_socket.pos().x() - self.loop_label.boundingRect().width() / 2,
                                   self.height - 18)
            self.loop_label.setToolTip("Виконати тіло циклу")

        out_end_socket = self.get_socket("out_end")
        if out_end_socket:
            self.end_label = QGraphicsTextItem("⏹️", self)
            self.end_label.setFont(QFont("Arial", 10))
            self.end_label.setPos(out_end_socket.pos().x() - self.end_label.boundingRect().width() / 2,
                                  self.height - 18)
            self.end_label.setToolTip("Завершити цикл")

    def update_display_properties(self, config=None):
        props = dict(self.properties)
        count = int(props.get('count', 0))
        text = f"Виконати {count} раз" if count > 0 else "Безкінечно (-1)" if count == -1 else "Не виконувати (0)"
        self.properties_text.setPlainText(text)

    def validate(self, config):
        props = dict(self.properties)
        try:
            count = int(props.get('count', 0))
            if count < -1:
                self.set_validation_state(False, "Кількість повторів не може бути меншою за -1.")
                return False
        except (ValueError, TypeError):
            self.set_validation_state(False, "Кількість повторів має бути числом.")
            return False

        out_loop_socket = self.get_socket("out_loop")
        out_end_socket = self.get_socket("out_end")

        if not out_loop_socket or not out_loop_socket.connections:
            self.set_validation_state(False, "Вихід 'Виконати' (▶️) повинен бути підключений.")
            return False

        # Вихід 'Завершити' може бути не підключений, якщо цикл безкінечний? Ні, краще вимагати завжди.
        if not out_end_socket or not out_end_socket.connections:
            self.set_validation_state(False, "Вихід 'Завершити' (⏹️) повинен бути підключений.")
            return False

        self.set_validation_state(True)
        return True


# --- Нові класи для Макросів ---

class MacroNode(BaseNode):
    ICON = "🧩" # Значок для макроса

    def __init__(self, macro_id=None, name="Макрос"):
        # Передаємо display name "Макрос" у super()
        super().__init__(name=name, node_type="Макрос", color=QColor("#7f8c8d"), icon=self.ICON) # Сірий колір
        self.macro_id = macro_id # ID визначення макроса в project_data['macros']
        # Початково сокетів немає, вони будуть додані update_sockets_from_definition

    def _create_sockets(self):
        # Макровузол не має сокетів за замовчуванням
        # Вони створюються динамічно
        pass

    def update_sockets_from_definition(self, macro_data):
        """Оновлює сокети вузла на основі визначення макросу."""
        self.clear_sockets() # Видаляємо старі сокети
        log.debug(f"Updating sockets for MacroNode {self.id} based on macro {macro_data.get('id')}")

        inputs = macro_data.get('inputs', [])
        outputs = macro_data.get('outputs', [])

        # Розраховуємо нову висоту та позиції
        num_inputs = len(inputs)
        num_outputs = len(outputs)
        socket_spacing = 25 # Відстань між сокетами
        min_height = 60 # Мінімальна висота
        required_height = max(min_height, socket_spacing * max(num_inputs, num_outputs) + 10)

        # Перевіряємо, чи змінилася висота
        if self.height != required_height:
            self.prepareGeometryChange() # Повідомляємо про зміну геометрії
            self.height = required_height
            self.rect.setRect(0, 0, self.width, self.height) # Оновлюємо прямокутник
            # Оновлюємо позицію індикатора помилки, якщо він є
            if hasattr(self, 'error_icon'):
                 self.error_icon.setPos(self.width - 24, 2)


        # Створюємо вхідні сокети зліва
        for i, input_def in enumerate(inputs):
            socket_name = input_def['name'] # Використовуємо ім'я входу як ім'я сокету
            y_pos = (i + 1) * required_height / (num_inputs + 1)
            self.add_socket(name=socket_name, is_output=False, position=QPointF(0, y_pos), display_name=socket_name)
            log.debug(f"  Added input socket: '{socket_name}' at y={y_pos}")

        # Створюємо вихідні сокети справа
        for i, output_def in enumerate(outputs):
            socket_name = output_def['name']
            y_pos = (i + 1) * required_height / (num_outputs + 1)
            self.add_socket(name=socket_name, is_output=True, position=QPointF(self.width, y_pos), display_name=socket_name)
            log.debug(f"  Added output socket: '{socket_name}' at y={y_pos}")

        self.update() # Оновлюємо вигляд

    def update_display_properties(self, config=None):
        # Показуємо ім'я макросу, до якого він прив'язаний
        macro_name = "Не визначено"
        main_window = None
        if self.scene() and self.scene().views():
            view = self.scene().views()[0]
            if hasattr(view, 'parent') and callable(view.parent):
                parent_widget = view.parent()
                # Проверяем, является ли родитель MainWindow
                if 'main_window' in str(type(parent_widget)): # Не самый лучший способ, но работает
                    main_window = parent_widget

        if self.macro_id and main_window and hasattr(main_window, 'project_data'):
              macro_def = main_window.project_data.get('macros', {}).get(self.macro_id)
              if macro_def:
                   macro_name = macro_def.get('name', 'Без імені')
        self.properties_text.setPlainText(f"Макрос: {macro_name}\nID: {self.macro_id or '?'}")


    def validate(self, config):
         main_window = None
         if self.scene() and self.scene().views():
             view = self.scene().views()[0]
             if hasattr(view, 'parent') and callable(view.parent):
                 parent_widget = view.parent()
                 if 'main_window' in str(type(parent_widget)):
                     main_window = parent_widget

         if not self.macro_id:
              self.set_validation_state(False, "Макровузол не прив'язаний до визначення макросу (відсутній macro_id).")
              return False
         elif not main_window or not hasattr(main_window, 'project_data') or self.macro_id not in main_window.project_data.get('macros', {}):
              self.set_validation_state(False, f"Визначення макросу з ID '{self.macro_id}' не знайдено в проекті.")
              return False
         else:
              macro_data = main_window.project_data['macros'][self.macro_id]
              defined_input_names = {inp['name'] for inp in macro_data.get('inputs', [])}
              defined_output_names = {out['name'] for out in macro_data.get('outputs', [])}
              current_input_names = {sock.socket_name for sock in self.get_input_sockets()}
              current_output_names = {sock.socket_name for sock in self.get_output_sockets()}

              if defined_input_names != current_input_names or defined_output_names != current_output_names:
                   log.warning(f"Sockets on MacroNode {self.id} do not match definition {self.macro_id}. Attempting update.")
                   try:
                       self.update_sockets_from_definition(macro_data)
                       current_input_names = {sock.socket_name for sock in self.get_input_sockets()}
                       current_output_names = {sock.socket_name for sock in self.get_output_sockets()}
                       if defined_input_names != current_input_names or defined_output_names != current_output_names:
                           self.set_validation_state(False, "Невідповідність сокетів визначенню макросу (після спроби оновлення).")
                           return False
                       else:
                            self.set_validation_state(True)
                            return True
                   except Exception as e:
                        log.error(f"Error auto-updating sockets for MacroNode {self.id}: {e}", exc_info=True)
                        self.set_validation_state(False, "Помилка оновлення сокетів за визначенням макросу.")
                        return False

              self.set_validation_state(True)
              return True


class MacroInputNode(BaseNode):
    ICON = "▶️" # Значок входу

    def __init__(self, name="Вхід"):
        log.debug(f"Initializing MacroInputNode with name: {name}")
        super().__init__(name=name, node_type="Вхід Макроса", color=QColor("#1abc9c"), icon=self.ICON)
        self.height = 50
        self._create_elements()
        # --- ИСПРАВЛЕНО: Пересоздаем сокеты после изменения высоты ---
        self.clear_sockets() # Удаляем сокеты, созданные BaseNode с неправильной высотой
        self._create_sockets() # Создаем сокеты заново с правильной высотой

    def _create_sockets(self):
        # Этот метод вызывается из __init__ (и из super().__init__),
        # поэтому он должен правильно работать с текущей высотой self.height
        log.debug(f"MacroInputNode._create_sockets for {self.id} with height {getattr(self, 'height', 85)}")
        self.add_socket("out", is_output=True, position=QPointF(self.width / 2, self.height), display_name="Вихід")

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
        self.icon_text.setPos((self.width - icon_rect.width()) / 2, (self.height - icon_rect.height()) / 2 - 10) # Трохи вище

        self.name_text = QGraphicsTextItem(self.node_name, self)
        self.name_text.setDefaultTextColor(QColor("#f0f0f0"));
        self.name_text.setFont(QFont("Arial", 9, QFont.Weight.Bold));
        # Центрування назви під іконкою
        name_rect = self.name_text.boundingRect()
        self.name_text.setPos((self.width - name_rect.width()) / 2, self.height - name_rect.height() - 5)


class MacroOutputNode(BaseNode):
    ICON = "⏹️" # Значок виходу

    def __init__(self, name="Вихід"):
        log.debug(f"Initializing MacroOutputNode with name: {name}")
        super().__init__(name=name, node_type="Вихід Макроса", color=QColor("#e67e22"), icon=self.ICON) # Помаранчевий
        self.height = 50
        self._create_elements()
        # --- ИСПРАВЛЕНО: Пересоздаем сокеты для консистентности, хотя здесь это не критично ---
        self.clear_sockets()
        self._create_sockets()

    def _create_sockets(self):
        log.debug(f"MacroOutputNode._create_sockets for {self.id}")
        # Входной сокет находится в y=0, поэтому изменение высоты на него не влияет,
        # но для надежности лучше пересоздать.
        self.add_socket("in", position=QPointF(self.width / 2, 0), display_name="Вхід")

    def _create_elements(self):
        # Спрощений вигляд, аналогічний входу
        self.rect = QGraphicsRectItem(0, 0, self.width, self.height, self)
        self.rect.setBrush(self.node_color);
        self.rect.setPen(QPen(Qt.GlobalColor.black, 1))

        self.icon_text = QGraphicsTextItem(self.node_icon, self)
        self.icon_text.setDefaultTextColor(Qt.GlobalColor.white);
        self.icon_text.setFont(QFont("Arial", 16))
        icon_rect = self.icon_text.boundingRect()
        self.icon_text.setPos((self.width - icon_rect.width()) / 2, (self.height - icon_rect.height()) / 2 - 10) # Трохи вище

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


class EditableTextItem(QGraphicsTextItem):
    """ A QGraphicsTextItem that becomes editable on double click. """

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        # Дозволяємо перенесення слів
        self.document().setDefaultTextOption(QTextOption(Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap))


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
        # Якщо це текст коментаря чи фрейму, можливо, оновити дані батька?
        parent = self.parentItem()
        if isinstance(parent, (CommentItem, FrameItem)):
             # Оновлюємо внутрішній текст батька, щоб він зберігся
             parent._text = self.toPlainText()
        super().focusOutEvent(event)

    # Додаємо обробник зміни тексту, щоб оновлювати дані батька в реальному часі
    def keyPressEvent(self, event):
         super().keyPressEvent(event)
         parent = self.parentItem()
         if isinstance(parent, (CommentItem, FrameItem)):
              parent._text = self.toPlainText() # Оновлюємо дані при зміні


class CommentItem(QGraphicsItem):
    def __init__(self, text="Коментар", width=200, height=100, view=None):
        super().__init__()
        self.id = generate_short_id() # Используем короткий ID
        self._width, self._height, self.view = max(60, width), max(40, height), view # Min size
        self._text = text if text is not None else "" # Ensure string
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                      QGraphicsItem.GraphicsItemFlag.ItemSendsScenePositionChanges)
        self.setZValue(-1) # Коментарі під вузлами, але над фреймами
        self.rect = QGraphicsRectItem(0, 0, self._width, self._height, self)
        self.rect.setBrush(QColor(255, 250, 170, 200)) # Жовтуватий напівпрозорий
        self.rect.setPen(QPen(QColor("#333333"), 1, Qt.PenStyle.DashLine))

        self.text_item = EditableTextItem(self._text, self)
        self.text_item.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.text_item.setPos(5, 5);
        self.text_item.setTextWidth(self._width - 10) # Задаємо ширину для переносу

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
        # Коментарі не повинні містити вузли логічно
        return []

    def boundingRect(self):
        # Додаємо невеликий запас для рамки виділення та ручки зміни розміру
        handle_size = self.resize_handle.rect().width() if hasattr(self, 'resize_handle') else 10
        margin = 2 + handle_size / 2
        return QRectF(0, 0, self._width, self._height).adjusted(-margin, -margin, margin, margin)


    def paint(self, painter, option, widget):
        pass # Елементи малюють себе самі

    def set_dimensions(self, width, height):
        # Встановлюємо мінімальні розміри
        new_width = max(60, width)
        new_height = max(40, height)
        if new_width == self._width and new_height == self._height:
             return # Розміри не змінились

        self.prepareGeometryChange()
        self._width, self._height = new_width, new_height
        self.rect.setRect(0, 0, self._width, self._height)
        self.text_item.setTextWidth(self._width - 10) # Оновлюємо ширину тексту
        handle_size = self.resize_handle.rect().width()
        self.resize_handle.setPos(self._width - handle_size, self._height - handle_size)
        self.update() # Оновлюємо вигляд

    def mousePressEvent(self, event):
        # Перевіряємо, чи клік був на ручці зміни розміру
        if self.resize_handle.sceneBoundingRect().contains(event.scenePos()):
            self.is_resizing = True
            self.start_resize_dims = (self._width, self._height)
            self.start_mouse_pos = event.scenePos()
            event.accept() # Поглинаємо подію, щоб не почалося перетягування
        else:
            super().mousePressEvent(event) # Стандартна обробка (перетягування)

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
            # Створюємо команду зміни розміру, якщо розмір змінився
            if self.view and self.start_resize_dims and new_dims != self.start_resize_dims:
                self.view.create_resize_command(self, self.start_resize_dims, new_dims)
            self.start_resize_dims = None;
            self.start_mouse_pos = None
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedChange:
            is_selected = value
            self.rect.setPen(QPen(QColor("#fffc42"), 2, Qt.PenStyle.SolidLine) if is_selected
                             else QPen(QColor("#333333"), 1, Qt.PenStyle.DashLine))
            # Показуємо ручку зміни розміру тільки коли вибрано
            self.resize_handle.setVisible(is_selected)
            self.setZValue(0 if is_selected else -1) # Вибрані коментарі вище не вибраних
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
        # Переконуємось, що розміри - це числа
        width = float(data.get('size', [200, 100])[0])
        height = float(data.get('size', [200, 100])[1])
        comment = cls(data.get('text', ''), width, height, view)
        comment.id = data.get('id', generate_short_id()) # Используем короткий ID
        comment.setPos(QPointF(*data.get('pos', (0,0))))
        comment.resize_handle.setVisible(False) # Сховати ручку спочатку
        return comment


class FrameItem(QGraphicsItem):
    def __init__(self, text="Новая группа", width=300, height=200, view=None):
        super().__init__()
        self.id = generate_short_id() # Используем короткий ID
        self.header_height = 30
        min_width = 100
        min_height = 60 + self.header_height
        self._width = max(min_width, width)
        self._height = max(min_height, height)
        self.view = view
        self._text = text if text is not None else "" # Ensure string

        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                      QGraphicsItem.GraphicsItemFlag.ItemSendsScenePositionChanges)
        self.setZValue(-2) # Фрейми найнижче
        self.rect = QGraphicsRectItem(0, 0, self._width, self._height, self)
        self.rect.setBrush(QColor(80, 80, 80, 180)) # Темно-сірий напівпрозорий
        self.rect.setPen(QPen(QColor(118, 185, 237), 1, Qt.PenStyle.DashLine)) # Світло-синя рамка

        self.header = QGraphicsRectItem(0, 0, self._width, self.header_height, self)
        self.header.setBrush(QColor(118, 185, 237, 220)) # Непрозорий заголовок
        self.header.setPen(QPen(Qt.PenStyle.NoPen))

        self.text_item = EditableTextItem(self._text, self)
        self.text_item.setDefaultTextColor(Qt.GlobalColor.white)
        font = QFont("Arial", 10, QFont.Weight.Bold)
        self.text_item.setFont(font)
        self.text_item.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.text_item.setPos(5, (self.header_height - self.text_item.boundingRect().height()) / 2) # Центруємо вертикально в заголовку
        self.text_item.setTextWidth(self._width - 10)

        handle_size = 10
        self.resize_handle = QGraphicsRectItem(0, 0, handle_size, handle_size, self)
        self.resize_handle.setBrush(QColor("#aaaaaa"))
        self.resize_handle.setPen(QPen(Qt.GlobalColor.black, 1))
        self.resize_handle.setPos(self._width - handle_size, self._height - handle_size)
        self.resize_handle.setZValue(1) # Ручка над основним прямокутником
        self.resize_handle.setVisible(False) # Сховати спочатку

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
            # Перецентрувати текст у заголовку після зміни
            self.text_item.setPos(5, (self.header_height - self.text_item.boundingRect().height()) / 2)

    def get_contained_nodes(self):
        """Знаходить вузли та коментарі, центри яких знаходяться всередині фрейму."""
        contained = []
        if not self.scene(): return contained
        frame_rect = self.sceneBoundingRect()
        # Шукаємо тільки BaseNode та CommentItem
        items_to_check = [item for item in self.scene().items(frame_rect) if isinstance(item, (BaseNode, CommentItem)) and item is not self]

        for item in items_to_check:
             item_center = item.sceneBoundingRect().center()
             # Перевіряємо чи центр елемента всередині фрейму (не включаючи межі)
             if frame_rect.contains(item_center):
                  contained.append(item)
        return contained


    def boundingRect(self):
        # Додаємо запас для ручки зміни розміру та рамки виділення
        handle_size = self.resize_handle.rect().width() if hasattr(self, 'resize_handle') else 10
        margin = 2 + handle_size / 2
        return QRectF(0, 0, self._width, self._height).adjusted(-margin, -margin, margin, margin)


    def paint(self, painter, option, widget):
        pass # Елементи малюють себе самі


    def set_dimensions(self, width, height):
        # Встановлюємо мінімальні розміри
        min_width = 100
        min_height = self.header_height + 30 # Заголовок + трохи місця
        new_width = max(min_width, width)
        new_height = max(min_height, height)
        if new_width == self._width and new_height == self._height:
            return

        self.prepareGeometryChange() # Важливо викликати ДО зміни розмірів
        self._width, self._height = new_width, new_height
        self.rect.setRect(0, 0, self._width, self._height)
        self.header.setRect(0, 0, self._width, self.header_height)
        self.text_item.setTextWidth(self._width - 10)
        # Перецентрувати текст після зміни ширини
        self.text_item.setPos(5, (self.header_height - self.text_item.boundingRect().height()) / 2)
        handle_size = self.resize_handle.rect().width()
        self.resize_handle.setPos(self._width - handle_size, self._height - handle_size)
        self.update() # Оновлюємо вигляд


    def mousePressEvent(self, event):
        # Перевіряємо ручку зміни розміру за її геометрією відносно фрейму
        handle_rect = self.resize_handle.rect()
        handle_pos_in_item = self.resize_handle.pos()
        handle_scene_rect = QRectF(self.scenePos() + handle_pos_in_item, handle_rect.size())

        if handle_scene_rect.contains(event.scenePos()):
            self.is_resizing = True
            self.start_resize_dims = (self._width, self._height)
            self.start_mouse_pos = event.scenePos()
            log.debug("Frame resize started")
            event.accept()
        else:
            # Зберігаємо позиції ДО виклику super(), бо він може змінити self.pos()
            self._contained_start_positions = {node: node.pos() for node in self.get_contained_nodes()}
            super().mousePressEvent(event) # Дозволяємо стандартне перетягування


    def mouseMoveEvent(self, event):
        if self.is_resizing:
            if self.start_mouse_pos: # Перевірка, що ініціалізація відбулася
                 delta = event.scenePos() - self.start_mouse_pos
                 self.set_dimensions(self.start_resize_dims[0] + delta.x(), self.start_resize_dims[1] + delta.y())
                 event.accept()
            else:
                 log.warning("Frame resize move event before press initialized properly.")
        else:
            # Обробка переміщення внутрішніх елементів
            old_pos = self.pos() # Позиція до переміщення базовим класом
            super().mouseMoveEvent(event) # Базовий клас переміщує сам фрейм
            new_pos = self.pos() # Нова позиція
            delta = new_pos - old_pos

            if delta.manhattanLength() > 0.1: # Якщо було реальне переміщення
                 selected_items = self.scene().selectedItems() if self.scene() else []
                 # Переміщуємо тільки ті внутрішні елементи, які НЕ вибрані разом з фреймом
                 for node, start_pos in self._contained_start_positions.items():
                     if node not in selected_items and node.scene() == self.scene(): # Перевіряємо, чи вузол ще на сцені
                          # Перевіряємо, чи вузол все ще візуально всередині (опціонально, може бути складним)
                          # if self.sceneBoundingRect().contains(node.sceneBoundingRect().center()):
                          node.setPos(node.pos() + delta) # Просто додаємо зміщення


    def mouseReleaseEvent(self, event):
        if self.is_resizing:
            self.is_resizing = False
            log.debug("Frame resize finished")
            new_dims = (self._width, self._height)
            # Створюємо команду зміни розміру, якщо розмір змінився
            if self.view and self.start_resize_dims and new_dims != self.start_resize_dims:
                self.view.create_resize_command(self, self.start_resize_dims, new_dims)
            self.start_resize_dims = None;
            self.start_mouse_pos = None
            event.accept()
        else:
            # Створюємо команду переміщення для фрейму ТА не вибраних внутрішніх елементів
            # Це потрібно робити в EditorView.mouseReleaseEvent, бо тільки там є повна картина
            # Очищуємо збережені позиції тут
            self._contained_start_positions = {}
            super().mouseReleaseEvent(event) # Дозволяємо стандартну обробку


    def mouseDoubleClickEvent(self, event):
        # Дозволяємо редагування тільки при подвійному кліку на заголовку
        if event.pos().y() < self.header_height:
            self.text_item.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
            self.text_item.setFocus(Qt.FocusReason.MouseFocusReason)
            cursor = self.text_item.textCursor()
            cursor.select(QTextCursor.SelectionType.Document)
            self.text_item.setTextCursor(cursor)
        else:
            # Не передаємо подію далі, щоб уникнути небажаної поведінки
            # super().mouseDoubleClickEvent(event)
            pass


    def itemChange(self, change, value):
        # Handle selection highlight
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedChange:
            is_selected = value
            pen = QPen(QColor("#fffc42"), 2, Qt.PenStyle.SolidLine) if is_selected \
                else QPen(QColor(118, 185, 237), 1, Qt.PenStyle.DashLine)
            self.rect.setPen(pen)
            # Показуємо/ховаємо ручку зміни розміру
            self.resize_handle.setVisible(is_selected)
            # Піднімаємо вибраний фрейм трохи вище не вибраних, але все ще низько
            self.setZValue(-1 if is_selected else -2)


        # Переміщення внутрішніх елементів обробляється в mouseMoveEvent
        # Тут не потрібно відстежувати ItemPositionChange/ItemPositionHasChanged

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
        # Переконуємось, що розміри - це числа
        width = float(data.get('size', [300, 200])[0])
        height = float(data.get('size', [300, 200])[1])
        frame = cls(data.get('text', ''), width, height, view)
        frame.id = data.get('id', generate_short_id()) # Используем короткий ID
        frame.setPos(QPointF(*data.get('pos', (0,0))))
        frame.resize_handle.setVisible(False) # Сховати ручку спочатку
        return frame
