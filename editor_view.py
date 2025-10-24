import logging
from PyQt6.QtWidgets import QGraphicsView, QGraphicsPathItem, QMenu, QApplication
from PyQt6.QtGui import QPainter, QPen, QColor, QPainterPath, QAction, QCursor
from PyQt6.QtCore import Qt, QPointF, QLineF, QSize, QTimer
from functools import partial

from nodes import Socket, BaseNode, Connection, CommentItem, FrameItem, TriggerNode, DecoratorNode, NODE_REGISTRY, \
    MacroNode  # <-- Добавлено MacroNode
from commands import (AddConnectionCommand, MoveItemsCommand, RemoveItemsCommand,
                      AddNodeAndConnectCommand, ResizeCommand, AlignNodesCommand, AddFrameCommand, AddCommentCommand,
                      UngroupFrameCommand, CreateMacroCommand)  # Додано CreateMacroCommand
from minimap import Minimap

log = logging.getLogger(__name__)


class EditorView(QGraphicsView):
    def __init__(self, scene, undo_stack, parent=None):
        super().__init__(scene, parent)
        self.undo_stack = undo_stack
        self._is_interactive = True

        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.start_socket = None
        self.temp_line = None
        self.grid_size = 20
        self.grid_pen_light = QPen(QColor("#2C2C2C"), 0.5)
        self.grid_pen_dark = QPen(QColor("#202020"), 1.0)

        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)

        self._is_panning = False
        self.pan_last_pos = QPointF()

        self.moved_items = set()
        self.moved_items_start_pos = {}

        self.minimap = Minimap(self)
        self.minimap.setVisible(True)
        self._update_minimap_timer = QTimer(self)
        self._update_minimap_timer.setInterval(100)
        self._update_minimap_timer.timeout.connect(self.update_minimap)
        self._update_minimap_timer.start()

    def set_interactive(self, is_interactive):
        self._is_interactive = is_interactive

    def create_resize_command(self, item, old_dims, new_dims):
        command = ResizeCommand(item, old_dims, new_dims)
        self.undo_stack.push(command)

    def update_minimap(self):
        self.minimap.update_view()

    def focus_on_item(self, item_to_focus):
        """
        Виділяє вказаний елемент і центрує на ньому вигляд.
        """
        log.debug(f"Focusing on item: {item_to_focus.id if hasattr(item_to_focus, 'id') else item_to_focus}")
        if not item_to_focus or not item_to_focus.scene():
            log.warning("focus_on_item: Item is invalid or not in scene.")
            return

        # Блокуємо сигнали, щоб уникнути рекурсивного виклику on_selection_changed
        self.scene().blockSignals(True)
        self.scene().clearSelection()
        item_to_focus.setSelected(True)
        self.scene().blockSignals(False)

        # Викликаємо on_selection_changed вручну, щоб оновити панель властивостей
        if hasattr(self.parent(), 'on_selection_changed'):
            self.parent().on_selection_changed()

        self.centerOn(item_to_focus)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        minimap_size = QSize(220, 160)
        margin = 10
        self.minimap.setGeometry(
            self.width() - minimap_size.width() - margin,
            self.height() - minimap_size.height() - margin,
            minimap_size.width(),
            minimap_size.height()
        )

    def drawBackground(self, painter, rect):
        super().drawBackground(painter, rect)
        left, right = int(rect.left()), int(rect.right())
        top, bottom = int(rect.top()), int(rect.bottom())
        first_left = left - (left % self.grid_size)
        first_top = top - (top % self.grid_size)
        lines_light, lines_dark = [], []
        for x in range(first_left, right, self.grid_size):
            (lines_dark if x % (self.grid_size * 5) == 0 else lines_light).append(QLineF(x, top, x, bottom))
        for y in range(first_top, bottom, self.grid_size):
            (lines_dark if y % (self.grid_size * 5) == 0 else lines_light).append(QLineF(left, y, right, y))
        painter.setPen(self.grid_pen_light);
        painter.drawLines(lines_light)
        painter.setPen(self.grid_pen_dark);
        painter.drawLines(lines_dark)

    def mousePressEvent(self, event):
        if not self._is_interactive:
            return
        if event.button() == Qt.MouseButton.MiddleButton:
            self._is_panning = True
            self.pan_last_pos = event.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return

        item_under_cursor = self.itemAt(event.pos())

        # High priority: Socket for new connection
        if isinstance(item_under_cursor, Socket):
            self.start_socket = item_under_cursor
            self.temp_line = QGraphicsPathItem()
            self.temp_line.setPen(QPen(QColor("#f0f0f0"), 2))
            self.scene().addItem(self.temp_line)
            self._update_potential_connections_highlight(self.start_socket)
            event.accept()
            return

        # If we clicked on empty space, handle deselection and rubber band
        if not item_under_cursor:
            is_ctrl_pressed = event.modifiers() & Qt.KeyboardModifier.ControlModifier
            if not is_ctrl_pressed:
                self.scene().clearSelection()
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
            super().mousePressEvent(event)
            return

        # If we clicked an item, let the base class handle selection and prepare for moving
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        super().mousePressEvent(event)

        # Record start positions for undo command
        self.moved_items = set(self.scene().selectedItems())
        if self.moved_items:
            self.moved_items_start_pos = {item: item.pos() for item in self.moved_items}
        else:
            self.moved_items_start_pos.clear()

    def mouseMoveEvent(self, event):
        if not self._is_interactive:
            return
        if self._is_panning:
            delta = event.pos() - self.pan_last_pos
            self.pan_last_pos = event.pos()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            return

        # Handle our custom line drawing.
        if self.start_socket and self.temp_line:
            p1 = self.start_socket.scenePos()
            p2 = self.mapToScene(event.pos())
            path = QPainterPath(p1)

            # Розрахунок контрольних точок для кривої Безьє (з update_path)
            dx = p2.x() - p1.x()
            dy = p2.y() - p1.y()
            ctrl1 = p1 + QPointF(0, dy * 0.5)
            ctrl2 = p2 - QPointF(0, dy * 0.5)
            threshold = 50
            if abs(dy) < threshold:
                offset_x = max(50, abs(dx) * 0.2)
                ctrl1 = p1 + QPointF(offset_x, threshold)
                ctrl2 = p2 - QPointF(offset_x, threshold)

            path.cubicTo(ctrl1, ctrl2, p2)
            self.temp_line.setPath(path)
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if not self._is_interactive:
            return
        if event.button() == Qt.MouseButton.MiddleButton:
            self._is_panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return

        # Call super first to finalize rubber band or other default actions
        super().mouseReleaseEvent(event)
        # Check if the event was accepted by the base class (e.g., rubber band selection finished)
        # If accepted, and it wasn't a connection attempt, return early.
        if event.isAccepted() and not (self.start_socket and self.temp_line):
            # Finalize move command if needed AFTER super() handled selection/drag
            moved_items_map = {}
            for item, start_pos in self.moved_items_start_pos.items():
                # Check if item still exists and position changed
                if item.scene() and item.pos() != start_pos:
                    moved_items_map[item] = (start_pos, item.pos())
            if moved_items_map:
                command = MoveItemsCommand(moved_items_map)
                self.undo_stack.push(command)

            self.moved_items.clear()
            self.moved_items_start_pos.clear()
            return  # Don't process connection logic if base handled it

        # Handle the end of our custom connection drawing.
        if self.start_socket and self.temp_line:
            start_node = self.start_socket.parentItem()  # Get node for ID
            start_node_id = start_node.id if start_node else None
            start_socket_name = self.start_socket.socket_name

            end_socket = next(
                (item for item in self.items(event.pos()) if isinstance(item, Socket) and item.is_highlighted),
                None)
            self._update_potential_connections_highlight(None)  # Reset highlight regardless of outcome
            if self.temp_line.scene(): self.scene().removeItem(self.temp_line)  # Remove temp line

            if end_socket:
                command = AddConnectionCommand(self.scene(), self.start_socket, end_socket)
                self.undo_stack.push(command)
            elif start_node_id:  # Only show menu if we started from a valid node
                item_at_pos = self.itemAt(event.pos())
                if item_at_pos is None:  # Only show menu on empty space
                    self._show_add_node_menu_on_drag(event, start_node_id, start_socket_name)

            # Reset state variables
            self.temp_line, self.start_socket = None, None
            event.accept()  # Mark event as handled
            # Clear move tracking as well, connection attempt takes precedence
            self.moved_items.clear()
            self.moved_items_start_pos.clear()
            return

        # If we were dragging items (and it wasn't accepted by super() or a connection attempt), create the move command.
        moved_items_map = {}
        for item, start_pos in self.moved_items_start_pos.items():
            if item.scene() and item.pos() != start_pos:
                moved_items_map[item] = (start_pos, item.pos())

        if moved_items_map:
            command = MoveItemsCommand(moved_items_map)
            self.undo_stack.push(command)

        # Clear tracking variables
        self.moved_items.clear()
        self.moved_items_start_pos.clear()
        # Don't accept event here if nothing happened, let it propagate if needed

    # --- ИСПРАВЛЕНИЕ 1: Добавляем обработчик двойного клика ---
    def mouseDoubleClickEvent(self, event):
        if not self._is_interactive:
            return

        item_under_cursor = self.itemAt(event.pos())
        node = item_under_cursor
        # Ищем родительский узел (BaseNode), если кликнули на дочерний элемент
        while node and not isinstance(node, BaseNode):
            node = node.parentItem()

        if isinstance(node, MacroNode):
            log.debug(f"Double-clicked on MacroNode {node.id}. Attempting to edit macro {node.macro_id}")
            main_window = self.parent()
            if hasattr(main_window, 'edit_macro'):
                # --- ИСПРАВЛЕНИЕ: Проверяем, существует ли macro_id ---
                if node.macro_id:
                    main_window.edit_macro(node.macro_id)
                    event.accept()
                    return
                else:
                    log.warning(f"MacroNode {node.id} has no macro_id assigned. Cannot edit.")
                    # Можно добавить сообщение пользователю здесь
                    QMessageBox.warning(self, "Помилка", "Макровузол не прив'язаний до макросу.")
                    event.accept()  # Принимаем событие, чтобы оно не шло дальше
                    return

        # Если это был не MacroNode, передаем событие дальше
        # (чтобы сработал двойной клик на тексте комментария/фрейма)
        super().mouseDoubleClickEvent(event)

    # --- КОНЕЦ ИСПРАВЛЕНИЯ 1 ---

    def _show_add_node_menu_on_drag(self, event, start_node_id, start_socket_name):
        context_menu = QMenu(self)
        for node_name in sorted(NODE_REGISTRY.keys()):
            # Не додаємо тригери та внутрішні вузли макросів у це меню
            if NODE_REGISTRY[node_name] is TriggerNode or node_name in ["MacroInputNode", "MacroOutputNode",
                                                                        "Вхід Макроса", "Вихід Макроса"]:
                continue
            icon = getattr(NODE_REGISTRY[node_name], 'ICON', '●')
            action = QAction(f"{icon} {node_name}", self)
            callback = partial(self._add_node_and_connect,
                               node_type_name=node_name,
                               view_pos=event.pos(),
                               start_node_id=start_node_id,
                               start_socket_name=start_socket_name)
            action.triggered.connect(callback)
            context_menu.addAction(action)
        if context_menu.actions():
            context_menu.exec(self.mapToGlobal(event.pos()))

    def _add_node_and_connect(self, node_type_name, view_pos, start_node_id, start_socket_name):
        scene_pos = self.mapToScene(view_pos)
        command = AddNodeAndConnectCommand(self.scene(), node_type_name, scene_pos, start_node_id,
                                           start_socket_name)
        self.undo_stack.push(command)

    def _update_potential_connections_highlight(self, start_socket):
        for item in self.scene().items():
            if not isinstance(item, Socket): continue
            if not start_socket:
                item.set_highlight(False);
                continue

            # A connection is valid if it's between an output and an input socket
            is_valid = item is not start_socket and item.is_output != start_socket.is_output

            if is_valid:
                # Ensure parent nodes exist
                start_node = start_socket.parentItem()
                end_node = item.parentItem()
                if not start_node or not end_node:
                    is_valid = False
                else:
                    # An input socket can only have one connection
                    input_socket = item if not item.is_output else start_socket
                    if len(input_socket.connections) > 0:
                        is_valid = False

                    # Certain output sockets can also only have one connection
                    output_socket = item if item.is_output else start_socket
                    output_node = output_socket.parentItem()
                    # Trigger and Decorator nodes (like Repeat) have restricted outputs
                    if isinstance(output_node, (TriggerNode, DecoratorNode)):
                        # Check specifically for 'out' on Trigger and 'out_loop'/'out_end' on Decorator
                        # Condition node outputs ('out_true'/'out_false') can have multiple connections
                        if output_socket.socket_name in ('out', 'out_loop', 'out_end') and len(
                                output_socket.connections) > 0:
                            is_valid = False

            item.set_highlight(is_valid)

    def wheelEvent(self, event):
        if not self._is_interactive:
            return
        zoom = 1.25 if event.angleDelta().y() > 0 else 1 / 1.25
        self.scale(zoom, zoom)

    def keyPressEvent(self, event):
        if not self._is_interactive:
            return
        key_text = event.text().lower()

        if event.modifiers() == Qt.KeyboardModifier.ControlModifier and key_text in ('g', 'п'):
            self._group_selection_in_frame()
            return

        if not event.modifiers() and key_text in ('c', 'с'):
            self._add_comment_at_cursor()
            return

        if event.key() == Qt.Key.Key_Delete:
            self._delete_selected_items()
        else:
            super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        if not self._is_interactive:
            return
        item_at_pos = self.itemAt(event.pos())
        logical_item = item_at_pos
        while logical_item and not isinstance(logical_item, (BaseNode, Connection, CommentItem, FrameItem)):
            logical_item = logical_item.parentItem()

        context_menu = QMenu(self)

        # Якщо клікнули на елемент, але він не вибраний, вибираємо тільки його
        if logical_item and not logical_item.isSelected():
            self.scene().clearSelection()
            logical_item.setSelected(True)
            log.debug(f"Context menu: Selected clicked item: {logical_item}")

        selected_items = self.scene().selectedItems()

        if selected_items:
            self._populate_item_actions_menu(context_menu, event)
        else:
            self._populate_add_node_menu(context_menu, event)
            self._populate_general_actions_menu(context_menu, event)

        if context_menu.actions():
            context_menu.exec(self.mapToGlobal(event.pos()))
        else:
            # Якщо меню порожнє, передаємо подію далі (рідкісний випадок)
            super().contextMenuEvent(event)

    def _populate_add_node_menu(self, parent_menu, event):
        add_node_menu = parent_menu.addMenu("Додати вузол")
        for node_name in sorted(NODE_REGISTRY.keys()):
            # Не додаємо внутрішні вузли макросів у це меню
            if node_name in ["MacroInputNode", "MacroOutputNode", "Вхід Макроса", "Вихід Макроса"]:
                continue
            icon = getattr(NODE_REGISTRY[node_name], 'ICON', '●')
            action = QAction(f"{icon} {node_name}", self)
            action.triggered.connect(
                lambda checked=False, name=node_name: self._add_node_from_context_menu(name, event.pos()))
            add_node_menu.addAction(action)

    def _populate_general_actions_menu(self, parent_menu, event):
        if QApplication.clipboard().text():
            parent_menu.addSeparator()
            paste_action = parent_menu.addAction("Вставити")
            paste_action.triggered.connect(lambda: self.parent().paste_selection(event.pos()))

    def _populate_item_actions_menu(self, parent_menu, event):
        selected_items = self.scene().selectedItems()
        selected_nodes = [item for item in selected_items if isinstance(item, BaseNode)]
        selected_nodes_or_comments = [item for item in selected_items if isinstance(item, (BaseNode, CommentItem))]

        is_frame_selected = any(isinstance(item, FrameItem) for item in selected_items)

        # Дії для Фреймів
        if is_frame_selected and len(selected_items) == 1:  # Тільки якщо вибрано один фрейм
            ungroup_action = parent_menu.addAction("Розгрупувати фрейм")
            ungroup_action.triggered.connect(self._ungroup_selected_frame)
            parent_menu.addSeparator()
        elif len(selected_nodes_or_comments) > 1 and not is_frame_selected:  # Не групувати, якщо вже є фрейм
            group_action = parent_menu.addAction("Сгрупувати в фрейм (Ctrl+G)")
            group_action.triggered.connect(self._group_selection_in_frame)
            parent_menu.addSeparator()

        # Дія для створення Макросу (додано)
        if len(selected_nodes) > 1:  # Потрібно вибрати хоча б два вузли для макросу
            create_macro_action = parent_menu.addAction("🧩 Створити Макрос...")
            create_macro_action.triggered.connect(self._create_macro_from_selection)
            parent_menu.addSeparator()

        # Дії вирівнювання
        if len(selected_nodes) > 1:
            align_menu = parent_menu.addMenu("Вирівняти")
            align_left = align_menu.addAction("По лівому краю")
            align_right = align_menu.addAction("По правому краю")
            align_h_center = align_menu.addAction("По горизонтальному центру")
            align_menu.addSeparator()
            align_top = align_menu.addAction("По верхньому краю")
            align_bottom = align_menu.addAction("По нижньому краю")
            align_v_center = align_menu.addAction("По вертикальному центру")
            align_left.triggered.connect(lambda: self._align_nodes('left'))
            align_right.triggered.connect(lambda: self._align_nodes('right'))
            align_h_center.triggered.connect(lambda: self._align_nodes('h_center'))
            align_top.triggered.connect(lambda: self._align_nodes('top'))
            align_bottom.triggered.connect(lambda: self._align_nodes('bottom'))
            align_v_center.triggered.connect(lambda: self._align_nodes('v_center'))
            parent_menu.addSeparator()  # Додано роздільник

        # Копіювати/Видалити
        can_copy = any(isinstance(item, BaseNode) for item in selected_items)
        if can_copy:
            copy_action = parent_menu.addAction("Копіювати")
            copy_action.triggered.connect(self.parent().copy_selection)

        if selected_items:
            # parent_menu.addSeparator() # Роздільник вже є або буде перед цим
            delete_action = parent_menu.addAction("Видалити")
            delete_action.triggered.connect(self._delete_selected_items)

    def _group_selection_in_frame(self):
        selected = [item for item in self.scene().selectedItems() if isinstance(item, (BaseNode, CommentItem))]
        if selected:
            command = AddFrameCommand(self.scene(), selected)
            self.undo_stack.push(command)

    def _ungroup_selected_frame(self):
        frame = next((item for item in self.scene().selectedItems() if isinstance(item, FrameItem)), None)
        if frame:
            command = UngroupFrameCommand(self.scene(), frame)
            self.undo_stack.push(command)

    # Нова функція для створення макросу
    def _create_macro_from_selection(self):
        selected_items = self.scene().selectedItems()
        # Передаємо головне вікно, оскільки команда повинна мати доступ до project_data
        main_window = self.parent()
        if selected_items and main_window:
            command = CreateMacroCommand(main_window, selected_items)
            self.undo_stack.push(command)
        else:
            log.warning("Cannot create macro: No items selected or main window not found.")

    def _align_nodes(self, mode):
        nodes = [item for item in self.scene().selectedItems() if isinstance(item, BaseNode)]
        if len(nodes) > 1:
            command = AlignNodesCommand(nodes, mode)
            self.undo_stack.push(command)

    def _add_node_from_context_menu(self, node_type_name, view_pos):
        self.parent().add_node(node_type_name, self.mapToScene(view_pos))

    def _add_comment_at_cursor(self):
        view_pos = self.mapFromGlobal(QCursor.pos())
        scene_pos = self.mapToScene(view_pos)
        command = AddCommentCommand(self.scene(), scene_pos, self)
        self.undo_stack.push(command)

    def _delete_selected_items(self):
        selected = self.scene().selectedItems()
        if selected:
            command = RemoveItemsCommand(self.scene(), selected)
            self.undo_stack.push(command)

