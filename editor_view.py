import logging
from PyQt6.QtWidgets import QGraphicsView, QGraphicsPathItem, QMenu, QApplication, QMessageBox # <-- Додано QMessageBox
from PyQt6.QtGui import QPainter, QPen, QColor, QPainterPath, QAction, QCursor
from PyQt6.QtCore import Qt, QPointF, QLineF, QSize, QTimer
from functools import partial

from nodes import Socket, BaseNode, Connection, CommentItem, FrameItem, TriggerNode, DecoratorNode, NODE_REGISTRY, \
    MacroNode  # <-- Добавлено MacroNode
# --- ВИДАЛЕНО: Імпорт commands ---
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
        log.debug("EditorView initialized.") # ДІАГНОСТИКА

    def set_interactive(self, is_interactive):
        self._is_interactive = is_interactive
        log.debug(f"EditorView interactive set to: {is_interactive}") # ДІАГНОСТИКА

    def create_resize_command(self, item, old_dims, new_dims):
        log.debug(f"Creating ResizeCommand for item {getattr(item, 'id', '?')}") # ДІАГНОСТИКА
        try:
            # --- Локальний імпорт ---
            from commands import ResizeCommand
            command = ResizeCommand(item, old_dims, new_dims)
            self.undo_stack.push(command)
            log.debug("  ResizeCommand pushed.") # ДІАГНОСТИКА
        except ImportError as e:
            log.error(f"  Failed to import ResizeCommand locally: {e}", exc_info=True) # ДІАГНОСТИКА
        except Exception as e:
            log.error(f"  Error creating/pushing ResizeCommand: {e}", exc_info=True) # ДІАГНОСТИКА


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
        log.debug("EditorView resized, minimap repositioned.") # ДІАГНОСТИКА

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
        log.debug(f"mousePressEvent: Button={event.button()}, Pos={event.pos()}") # ДІАГНОСТИКА
        if not self._is_interactive:
            log.debug("  Ignoring press event (not interactive).") # ДІАГНОСТИКА
            return
        if event.button() == Qt.MouseButton.MiddleButton:
            self._is_panning = True
            self.pan_last_pos = event.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            log.debug("  Starting pan.") # ДІАГНОСТИКА
            event.accept()
            return

        item_under_cursor = self.itemAt(event.pos())
        log.debug(f"  Item under cursor: {item_under_cursor}") # ДІАГНОСТИКА

        # High priority: Socket for new connection
        if isinstance(item_under_cursor, Socket):
            self.start_socket = item_under_cursor
            self.temp_line = QGraphicsPathItem()
            self.temp_line.setPen(QPen(QColor("#f0f0f0"), 2))
            self.scene().addItem(self.temp_line)
            self._update_potential_connections_highlight(self.start_socket)
            log.debug(f"  Starting connection from socket: {self.start_socket.socket_name} on {self.start_socket.parentItem().id}") # ДІАГНОСТИКА
            event.accept()
            return

        # If we clicked on empty space, handle deselection and rubber band
        if not item_under_cursor:
            is_ctrl_pressed = event.modifiers() & Qt.KeyboardModifier.ControlModifier
            if not is_ctrl_pressed:
                self.scene().clearSelection()
                log.debug("  Clicked empty space, cleared selection.") # ДІАГНОСТИКА
            else:
                log.debug("  Clicked empty space with Ctrl, keeping selection.") # ДІАГНОСТИКА
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
            log.debug("  Setting drag mode to RubberBandDrag.") # ДІАГНОСТИКА
            super().mousePressEvent(event)
            return

        # If we clicked an item, let the base class handle selection and prepare for moving
        log.debug("  Clicked on an item, setting drag mode to NoDrag and calling super().mousePressEvent.") # ДІАГНОСТИКА
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        super().mousePressEvent(event)

        # Record start positions for undo command AFTER super() potentially changes selection
        self.moved_items = set(self.scene().selectedItems())
        if self.moved_items:
            self.moved_items_start_pos = {item: item.pos() for item in self.moved_items}
            log.debug(f"  Recording start positions for {len(self.moved_items)} selected items.") # ДІАГНОСТИКА
        else:
            self.moved_items_start_pos.clear()
            log.debug("  No items selected after press, clearing move start positions.") # ДІАГНОСТИКА


    def mouseMoveEvent(self, event):
        # log.debug(f"mouseMoveEvent: Pos={event.pos()}") # Дуже багато повідомлень, закоментовано
        if not self._is_interactive:
            return
        if self._is_panning:
            delta = event.pos() - self.pan_last_pos
            self.pan_last_pos = event.pos()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            # log.debug(f"  Panning delta: {delta}") # Закоментовано
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
            # log.debug("  Drawing temporary connection line.") # Закоментовано
            return

        # log.debug("  Calling super().mouseMoveEvent.") # Закоментовано
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        log.debug(f"mouseReleaseEvent: Button={event.button()}, Pos={event.pos()}") # ДІАГНОСТИКА
        if not self._is_interactive:
            log.debug("  Ignoring release event (not interactive).") # ДІАГНОСТИКА
            return
        if event.button() == Qt.MouseButton.MiddleButton:
            self._is_panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            log.debug("  Ending pan.") # ДІАГНОСТИКА
            event.accept()
            return

        # Call super first to finalize rubber band or other default actions
        log.debug("  Calling super().mouseReleaseEvent.") # ДІАГНОСТИКА
        super().mouseReleaseEvent(event)
        log.debug(f"  super().mouseReleaseEvent finished. Event accepted: {event.isAccepted()}") # ДІАГНОСТИКА

        # Handle the end of our custom connection drawing.
        if self.start_socket and self.temp_line:
            log.debug("  Finishing connection attempt...") # ДІАГНОСТИКА
            start_node = self.start_socket.parentItem()  # Get node for ID
            start_node_id = start_node.id if start_node else None
            start_socket_name = self.start_socket.socket_name

            end_socket = next(
                (item for item in self.items(event.pos()) if isinstance(item, Socket) and item.is_highlighted),
                None)
            log.debug(f"  End socket found: {end_socket}") # ДІАГНОСТИКА
            self._update_potential_connections_highlight(None)  # Reset highlight regardless of outcome
            if self.temp_line.scene(): self.scene().removeItem(self.temp_line)  # Remove temp line
            log.debug("  Removed temporary line.") # ДІАГНОСТИКА

            if end_socket:
                log.debug(f"  Valid connection target found. Creating AddConnectionCommand...") # ДІАГНОСТИКА
                try:
                    # --- Локальний імпорт ---
                    from commands import AddConnectionCommand
                    command = AddConnectionCommand(self.scene(), self.start_socket, end_socket)
                    self.undo_stack.push(command)
                    log.debug("    AddConnectionCommand pushed.") # ДІАГНОСТИКА
                except ImportError as e:
                    log.error(f"    Failed to import AddConnectionCommand locally: {e}", exc_info=True) # ДІАГНОСТИКА
                except Exception as e:
                    log.error(f"    Error creating/pushing AddConnectionCommand: {e}", exc_info=True) # ДІАГНОСТИКА
            elif start_node_id:  # Only show menu if we started from a valid node
                item_at_pos = self.itemAt(event.pos())
                if item_at_pos is None:  # Only show menu on empty space
                    log.debug("  No valid end socket, showing add node menu.") # ДІАГНОСТИКА
                    self._show_add_node_menu_on_drag(event, start_node_id, start_socket_name)
                else:
                    log.debug("  Connection ended on an item, but not a valid socket.") # ДІАГНОСТИКА
            else:
                log.warning("  Connection attempt ended, but start_node_id was invalid.") # ДІАГНОСТИКА


            # Reset state variables
            self.temp_line, self.start_socket = None, None
            event.accept()  # Mark event as handled
            # Clear move tracking as well, connection attempt takes precedence
            self.moved_items.clear()
            self.moved_items_start_pos.clear()
            log.debug("  Connection attempt finished, event accepted.") # ДІАГНОСТИКА
            return

        # Check if the event was accepted by the base class (e.g., rubber band selection finished)
        # If accepted, and it wasn't a connection attempt (handled above), finalize move command.
        if event.isAccepted():
            log.debug("  Event was accepted by superclass (likely rubber band). Finalizing potential move.") # ДІАГНОСТИКА
            moved_items_map = {}
            for item, start_pos in self.moved_items_start_pos.items():
                if item.scene() and item.pos() != start_pos:
                    moved_items_map[item] = (start_pos, item.pos())
            if moved_items_map:
                log.debug(f"  Creating MoveItemsCommand for {len(moved_items_map)} items...") # ДІАГНОСТИКА
                try:
                    # --- Локальний імпорт ---
                    from commands import MoveItemsCommand
                    command = MoveItemsCommand(moved_items_map)
                    self.undo_stack.push(command)
                    log.debug("    MoveItemsCommand pushed.") # ДІАГНОСТИКА
                except ImportError as e:
                    log.error(f"    Failed to import MoveItemsCommand locally: {e}", exc_info=True) # ДІАГНОСТИКА
                except Exception as e:
                    log.error(f"    Error creating/pushing MoveItemsCommand: {e}", exc_info=True) # ДІАГНОСТИКА

            self.moved_items.clear()
            self.moved_items_start_pos.clear()
            log.debug("  Cleared move tracking after superclass accepted event.") # ДІАГНОСТИКА
            return  # Don't process further

        # If we reach here, it means we weren't panning, weren't finishing a connection,
        # and the superclass didn't accept the event (meaning it wasn't a rubber band drag).
        # This implies it was likely a regular item drag that just finished.
        log.debug("  Event not accepted by superclass, finalizing potential move from regular drag.") # ДІАГНОСТИКА
        moved_items_map = {}
        for item, start_pos in self.moved_items_start_pos.items():
            # Check if item still exists and position changed
            if item.scene() and item.pos() != start_pos:
                moved_items_map[item] = (start_pos, item.pos())
        if moved_items_map:
            log.debug(f"  Creating MoveItemsCommand for {len(moved_items_map)} items...") # ДІАГНОСТИКА
            try:
                # --- Локальний імпорт ---
                from commands import MoveItemsCommand
                command = MoveItemsCommand(moved_items_map)
                self.undo_stack.push(command)
                log.debug("    MoveItemsCommand pushed.") # ДІАГНОСТИКА
            except ImportError as e:
                log.error(f"    Failed to import MoveItemsCommand locally: {e}", exc_info=True) # ДІАГНОСТИКА
            except Exception as e:
                log.error(f"    Error creating/pushing MoveItemsCommand: {e}", exc_info=True) # ДІАГНОСТИКА

        # Clear tracking variables
        self.moved_items.clear()
        self.moved_items_start_pos.clear()
        log.debug("  Cleared move tracking after regular drag.") # ДІАГНОСТИКА
        # Don't accept event here if nothing happened, let it propagate if needed


    # --- ИСПРАВЛЕНИЕ 1: Добавляем обработчик двойного клика ---
    def mouseDoubleClickEvent(self, event):
        log.debug(f"mouseDoubleClickEvent: Pos={event.pos()}") # ДІАГНОСТИКА
        if not self._is_interactive:
            log.debug("  Ignoring double click event (not interactive).") # ДІАГНОСТИКА
            return

        item_under_cursor = self.itemAt(event.pos())
        node = item_under_cursor
        # Ищем родительский узел (BaseNode), если кликнули на дочерний элемент
        while node and not isinstance(node, BaseNode):
            node = node.parentItem()
        log.debug(f"  Logical item under cursor: {node}") # ДІАГНОСТИКА


        if isinstance(node, MacroNode):
            log.debug(f"Double-clicked on MacroNode {node.id}. Attempting to edit macro {node.macro_id}")
            main_window = self.parent()
            if hasattr(main_window, 'edit_macro'):
                # --- ИСПРАВЛЕНИЕ: Проверяем, существует ли macro_id ---
                if node.macro_id:
                    main_window.edit_macro(node.macro_id)
                    event.accept()
                    log.debug("  edit_macro called, event accepted.") # ДІАГНОСТИКА
                    return
                else:
                    log.warning(f"MacroNode {node.id} has no macro_id assigned. Cannot edit.")
                    # Можно добавить сообщение пользователю здесь
                    QMessageBox.warning(self, "Помилка", "Макровузол не прив'язаний до макросу.")
                    event.accept()  # Принимаем событие, чтобы оно не шло дальше
                    log.debug("  MacroNode has no macro_id, showed warning, event accepted.") # ДІАГНОСТИКА
                    return

        # Если это был не MacroNode, передаем событие дальше
        # (чтобы сработал двойной клик на тексте комментария/фрейма)
        log.debug("  Not a MacroNode or edit failed, calling super().mouseDoubleClickEvent.") # ДІАГНОСТИКА
        super().mouseDoubleClickEvent(event)

    # --- КОНЕЦ ИСПРАВЛЕНИЯ 1 ---

    def _show_add_node_menu_on_drag(self, event, start_node_id, start_socket_name):
        log.debug(f"Showing add node menu on drag release. Start node: {start_node_id}:{start_socket_name}") # ДІАГНОСТИКА
        context_menu = QMenu(self)
        added_action = False # ДІАГНОСТИКА
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
            added_action = True # ДІАГНОСТИКА
        if context_menu.actions():
            log.debug(f"  Menu has actions ({added_action}), executing...") # ДІАГНОСТИКА
            context_menu.exec(self.mapToGlobal(event.pos()))
        else:
            log.debug("  Menu has no actions, not executing.") # ДІАГНОСТИКА


    def _add_node_and_connect(self, node_type_name, view_pos, start_node_id, start_socket_name):
        log.debug(f"Adding node '{node_type_name}' and connecting from {start_node_id}:{start_socket_name} at pos {view_pos}") # ДІАГНОСТИКА
        try:
            # --- Локальний імпорт ---
            from commands import AddNodeAndConnectCommand
            scene_pos = self.mapToScene(view_pos)
            command = AddNodeAndConnectCommand(self.scene(), node_type_name, scene_pos, start_node_id,
                                                    start_socket_name)
            self.undo_stack.push(command)
            log.debug("  AddNodeAndConnectCommand pushed.") # ДІАГНОСТИКА
        except ImportError as e:
             log.error(f"  Failed to import AddNodeAndConnectCommand locally: {e}", exc_info=True) # ДІАГНОСТИКА
        except Exception as e:
            log.error(f"  Error creating/pushing AddNodeAndConnectCommand: {e}", exc_info=True) # ДІАГНОСТИКА


    def _update_potential_connections_highlight(self, start_socket):
        # log.debug(f"Updating connection highlights. Start socket: {start_socket.socket_name if start_socket else 'None'}") # Закоментовано
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
        # log.debug("  Highlight update finished.") # Закоментовано


    def wheelEvent(self, event):
        # log.debug(f"wheelEvent: Delta={event.angleDelta().y()}") # Закоментовано
        if not self._is_interactive:
            return
        zoom = 1.25 if event.angleDelta().y() > 0 else 1 / 1.25
        self.scale(zoom, zoom)
        # log.debug(f"  Scaled by {zoom}") # Закоментовано


    def keyPressEvent(self, event):
        log.debug(f"keyPressEvent: Key={event.key()}, Text='{event.text()}', Modifiers={event.modifiers()}") # ДІАГНОСТИКА
        if not self._is_interactive:
            log.debug("  Ignoring key press event (not interactive).") # ДІАГНОСТИКА
            return
        key_text = event.text().lower()

        if event.modifiers() == Qt.KeyboardModifier.ControlModifier and key_text in ('g', 'п'):
            log.debug("  Ctrl+G detected, grouping selection.") # ДІАГНОСТИКА
            self._group_selection_in_frame()
            return

        if not event.modifiers() and key_text in ('c', 'с'):
            log.debug("  'c' detected, adding comment.") # ДІАГНОСТИКА
            self._add_comment_at_cursor()
            return

        if event.key() == Qt.Key.Key_Delete:
            log.debug("  Delete key detected, deleting selection.") # ДІАГНОСТИКА
            self._delete_selected_items()
        else:
            log.debug("  Calling super().keyPressEvent.") # ДІАГНОСТИКА
            super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        log.debug(f"contextMenuEvent: Pos={event.pos()}") # ДІАГНОСТИКА
        if not self._is_interactive:
            log.debug("  Ignoring context menu event (not interactive).") # ДІАГНОСТИКА
            return
        item_at_pos = self.itemAt(event.pos())
        logical_item = item_at_pos
        while logical_item and not isinstance(logical_item, (BaseNode, Connection, CommentItem, FrameItem)):
            logical_item = logical_item.parentItem()
        log.debug(f"  Logical item for context menu: {logical_item}") # ДІАГНОСТИКА


        context_menu = QMenu(self)

        # Якщо клікнули на елемент, але він не вибраний, вибираємо тільки його
        if logical_item and not logical_item.isSelected():
            self.scene().clearSelection()
            logical_item.setSelected(True)
            log.debug(f"Context menu: Selected clicked item: {getattr(logical_item, 'id', logical_item)}") # ДІАГНОСТИКА


        selected_items = self.scene().selectedItems()
        log.debug(f"  Selected items count: {len(selected_items)}") # ДІАГНОСТИКА


        if selected_items:
            log.debug("  Populating item actions menu.") # ДІАГНОСТИКА
            self._populate_item_actions_menu(context_menu, event)
        else:
            log.debug("  Populating general actions menu.") # ДІАГНОСТИКА
            self._populate_add_node_menu(context_menu, event)
            self._populate_general_actions_menu(context_menu, event)

        if context_menu.actions():
            log.debug("  Executing context menu.") # ДІАГНОСТИКА
            context_menu.exec(self.mapToGlobal(event.pos()))
        else:
            # Якщо меню порожнє, передаємо подію далі (рідкісний випадок)
            log.debug("  Context menu is empty, calling super().contextMenuEvent.") # ДІАГНОСТИКА
            super().contextMenuEvent(event)

    def _populate_add_node_menu(self, parent_menu, event):
        log.debug("Populating add node context submenu...") # ДІАГНОСТИКА
        add_node_menu = parent_menu.addMenu("Додати вузол")
        action_added = False # ДІАГНОСТИКА
        for node_name in sorted(NODE_REGISTRY.keys()):
            # Не додаємо внутрішні вузли макросів у це меню
            if node_name in ["MacroInputNode", "MacroOutputNode", "Вхід Макроса", "Вихід Макроса"]:
                continue
            icon = getattr(NODE_REGISTRY[node_name], 'ICON', '●')
            action = QAction(f"{icon} {node_name}", self)
            action.triggered.connect(
                lambda checked=False, name=node_name: self._add_node_from_context_menu(name, event.pos()))
            add_node_menu.addAction(action)
            action_added = True # ДІАГНОСТИКА
        log.debug(f"  Added node actions: {action_added}") # ДІАГНОСТИКА


    def _populate_general_actions_menu(self, parent_menu, event):
        log.debug("Populating general context actions...") # ДІАГНОСТИКА
        clipboard_text = QApplication.clipboard().text() # Перевіряємо один раз
        if clipboard_text:
            # --- ДОДАНО: Перевірка валідності XML ---
            is_valid_xml = False
            try:
                ET.fromstring(clipboard_text.encode('utf-8'))
                is_valid_xml = True
            except ET.XMLSyntaxError:
                log.debug("  Clipboard content is not valid XML for pasting.") # ДІАГНОСТИКА
                pass # Не додаємо дію, якщо не XML
            except Exception as e:
                log.warning(f"  Error checking clipboard XML: {e}") # ДІАГНОСТИКА
                pass # Не додаємо дію при іншій помилці

            if is_valid_xml:
                 log.debug("  Adding Paste action.") # ДІАГНОСТИКА
                 parent_menu.addSeparator()
                 paste_action = parent_menu.addAction("Вставити")
                 paste_action.triggered.connect(lambda: self.parent().paste_selection(event.pos()))
            # --- КІНЕЦЬ ДОДАНОГО ---
        else:
            log.debug("  Clipboard is empty, no Paste action added.") # ДІАГНОСТИКА


    def _populate_item_actions_menu(self, parent_menu, event):
        log.debug("Populating item context actions...") # ДІАГНОСТИКА
        selected_items = self.scene().selectedItems()
        selected_nodes = [item for item in selected_items if isinstance(item, BaseNode)]
        selected_nodes_or_comments = [item for item in selected_items if isinstance(item, (BaseNode, CommentItem))]
        selected_macro_nodes = [item for item in selected_nodes if isinstance(item, MacroNode)] # <-- ДОДАНО

        is_frame_selected = any(isinstance(item, FrameItem) for item in selected_items)
        log.debug(f"  Item counts: Nodes={len(selected_nodes)}, NodesOrComments={len(selected_nodes_or_comments)}, Macros={len(selected_macro_nodes)}, IsFrame={is_frame_selected}") # ДІАГНОСТИКА


        # Дії для Фреймів
        if is_frame_selected and len(selected_items) == 1:  # Тільки якщо вибрано один фрейм
            log.debug("  Adding Ungroup Frame action.") # ДІАГНОСТИКА
            ungroup_action = parent_menu.addAction("Розгрупувати фрейм")
            ungroup_action.triggered.connect(self._ungroup_selected_frame)
            parent_menu.addSeparator()
        elif len(selected_nodes_or_comments) > 1 and not is_frame_selected:  # Не групувати, якщо вже є фрейм
            log.debug("  Adding Group Frame action.") # ДІАГНОСТИКА
            group_action = parent_menu.addAction("Сгрупувати в фрейм (Ctrl+G)")
            group_action.triggered.connect(self._group_selection_in_frame)
            parent_menu.addSeparator()

        # Дія для створення Макросу
        if len(selected_nodes) > 1:
            log.debug("  Adding Create Macro action.") # ДІАГНОСТИКА
            create_macro_action = parent_menu.addAction("🧩 Створити Макрос...")
            create_macro_action.triggered.connect(self._create_macro_from_selection)
            parent_menu.addSeparator()

        # --- ДОДАНО: Дія для розгрупування Макросу ---
        # Показуємо, тільки якщо вибрано РІВНО ОДИН MacroNode
        if len(selected_items) == 1 and len(selected_macro_nodes) == 1:
            log.debug("  Adding Ungroup Macro action.") # ДІАГНОСТИКА
            ungroup_macro_action = parent_menu.addAction("💥 Розгрупувати Макрос")
            ungroup_macro_action.triggered.connect(self._ungroup_selected_macro)
            parent_menu.addSeparator()
        # --- КІНЕЦЬ ДОДАНОГО ---


        # Дії вирівнювання
        if len(selected_nodes) > 1:
            log.debug("  Adding Align submenu.") # ДІАГНОСТИКА
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
            parent_menu.addSeparator()

        # Копіювати/Видалити
        can_copy = any(isinstance(item, (BaseNode, CommentItem, FrameItem)) for item in selected_items) # <-- ЗМІНА: Дозволяємо копіювати коментарі та фрейми
        if can_copy:
            log.debug("  Adding Copy action.") # ДІАГНОСТИКА
            copy_action = parent_menu.addAction("Копіювати")
            copy_action.triggered.connect(self.parent().copy_selection)

        if selected_items:
            log.debug("  Adding Delete action.") # ДІАГНОСТИКА
            delete_action = parent_menu.addAction("Видалити")
            delete_action.triggered.connect(self._delete_selected_items)
        log.debug("Finished populating item context actions.") # ДІАГНОСТИКА


    def _group_selection_in_frame(self):
        log.debug("Grouping selection in frame...") # ДІАГНОСТИКА
        try:
            # --- Локальний імпорт ---
            from commands import AddFrameCommand
            selected = [item for item in self.scene().selectedItems() if isinstance(item, (BaseNode, CommentItem))]
            if selected:
                command = AddFrameCommand(self.scene(), selected)
                self.undo_stack.push(command)
                log.debug("  AddFrameCommand pushed.") # ДІАГНОСТИКА
            else:
                log.debug("  No valid items selected for grouping.") # ДІАГНОСТИКА
        except ImportError as e:
             log.error(f"  Failed to import AddFrameCommand locally: {e}", exc_info=True) # ДІАГНОСТИКА
        except Exception as e:
             log.error(f"  Error creating/pushing AddFrameCommand: {e}", exc_info=True) # ДІАГНОСТИКА


    def _ungroup_selected_frame(self):
        log.debug("Ungrouping selected frame...") # ДІАГНОСТИКА
        try:
            # --- Локальний імпорт ---
            from commands import UngroupFrameCommand
            frame = next((item for item in self.scene().selectedItems() if isinstance(item, FrameItem)), None)
            if frame:
                command = UngroupFrameCommand(self.scene(), frame)
                self.undo_stack.push(command)
                log.debug(f"  UngroupFrameCommand pushed for frame {frame.id}.") # ДІАГНОСТИКА
            else:
                log.debug("  No frame selected for ungrouping.") # ДІАГНОСТИКА
        except ImportError as e:
             log.error(f"  Failed to import UngroupFrameCommand locally: {e}", exc_info=True) # ДІАГНОСТИКА
        except Exception as e:
             log.error(f"  Error creating/pushing UngroupFrameCommand: {e}", exc_info=True) # ДІАГНОСТИКА


    # Нова функція для створення макросу
    def _create_macro_from_selection(self):
        log.debug("Creating macro from selection...") # ДІАГНОСТИКА
        try:
            # --- Локальний імпорт ---
            from commands import CreateMacroCommand
            selected_items = self.scene().selectedItems()
            main_window = self.parent()
            if selected_items and main_window:
                command = CreateMacroCommand(main_window, selected_items)
                self.undo_stack.push(command)
                log.debug("  CreateMacroCommand pushed.") # ДІАГНОСТИКА
            else:
                log.warning("Cannot create macro: No items selected or main window not found.")
        except ImportError as e:
             log.error(f"  Failed to import CreateMacroCommand locally: {e}", exc_info=True) # ДІАГНОСТИКА
        except Exception as e:
             log.error(f"  Error creating/pushing CreateMacroCommand: {e}", exc_info=True) # ДІАГНОСТИКА


    # --- ДОДАНО: Нова функція для розгрупування макросу ---
    def _ungroup_selected_macro(self):
        """Викликає команду розгрупування для вибраного MacroNode."""
        log.debug("Ungrouping selected macro...") # ДІАГНОСТИКА
        try:
            # --- Локальний імпорт ---
            from commands import UngroupMacroCommand
            selected_macro_node = next((item for item in self.scene().selectedItems() if isinstance(item, MacroNode)), None)
            main_window = self.parent() # Потрібен доступ до ProjectManager
            if selected_macro_node and main_window:
                log.debug(f"Ungroup macro action triggered for MacroNode: {selected_macro_node.id}")
                # Перевіряємо, чи існує визначення макросу
                if not selected_macro_node.macro_id or not main_window.project_manager.get_macro_data(selected_macro_node.macro_id):
                     log.warning("Cannot ungroup macro: Definition not found or macro_id is missing.")
                     QMessageBox.warning(self, "Помилка", "Не вдалося знайти визначення для цього макросу.")
                     return
                command = UngroupMacroCommand(main_window, selected_macro_node)
                self.undo_stack.push(command)
                log.debug(f"  UngroupMacroCommand pushed for macro node {selected_macro_node.id}.") # ДІАГНОСТИКА
            else:
                log.warning("Ungroup macro action called but no single MacroNode selected or main window not found.")
        except ImportError as e:
             log.error(f"  Failed to import UngroupMacroCommand locally: {e}", exc_info=True) # ДІАГНОСТИКА
        except Exception as e:
             log.error(f"  Error creating/pushing UngroupMacroCommand: {e}", exc_info=True) # ДІАГНОСТИКА
    # --- КІНЕЦЬ ДОДАНОГО ---

    def _align_nodes(self, mode):
        log.debug(f"Aligning selected nodes (mode: {mode})...") # ДІАГНОСТИКА
        try:
            # --- Локальний імпорт ---
            from commands import AlignNodesCommand
            nodes = [item for item in self.scene().selectedItems() if isinstance(item, BaseNode)]
            if len(nodes) > 1:
                command = AlignNodesCommand(nodes, mode)
                self.undo_stack.push(command)
                log.debug("  AlignNodesCommand pushed.") # ДІАГНОСТИКА
            else:
                log.debug("  Not enough nodes selected for alignment.") # ДІАГНОСТИКА
        except ImportError as e:
             log.error(f"  Failed to import AlignNodesCommand locally: {e}", exc_info=True) # ДІАГНОСТИКА
        except Exception as e:
             log.error(f"  Error creating/pushing AlignNodesCommand: {e}", exc_info=True) # ДІАГНОСТИКА


    def _add_node_from_context_menu(self, node_type_name, view_pos):
        log.debug(f"Adding node '{node_type_name}' from context menu at {view_pos}.") # ДІАГНОСТИКА
        # Тут не потрібен локальний імпорт команди, бо викликається метод батька
        self.parent().add_node(node_type_name, self.mapToScene(view_pos))

    def _add_comment_at_cursor(self):
        log.debug("Adding comment at cursor...") # ДІАГНОСТИКА
        try:
            # --- Локальний імпорт ---
            from commands import AddCommentCommand
            view_pos = self.mapFromGlobal(QCursor.pos())
            scene_pos = self.mapToScene(view_pos)
            command = AddCommentCommand(self.scene(), scene_pos, self)
            self.undo_stack.push(command)
            log.debug(f"  AddCommentCommand pushed at scene pos {scene_pos}.") # ДІАГНОСТИКА
        except ImportError as e:
             log.error(f"  Failed to import AddCommentCommand locally: {e}", exc_info=True) # ДІАГНОСТИКА
        except Exception as e:
             log.error(f"  Error creating/pushing AddCommentCommand: {e}", exc_info=True) # ДІАГНОСТИКА


    def _delete_selected_items(self):
        log.debug("Deleting selected items...") # ДІАГНОСТИКА
        try:
            # --- Локальний імпорт ---
            from commands import RemoveItemsCommand
            selected = self.scene().selectedItems()
            if selected:
                command = RemoveItemsCommand(self.scene(), selected)
                self.undo_stack.push(command)
                log.debug(f"  RemoveItemsCommand pushed for {len(selected)} items.") # ДІАГНОСТИКА
            else:
                log.debug("  No items selected for deletion.") # ДІАГНОСТИКА
        except ImportError as e:
             log.error(f"  Failed to import RemoveItemsCommand locally: {e}", exc_info=True) # ДІАГНОСТИКА
        except Exception as e:
             log.error(f"  Error creating/pushing RemoveItemsCommand: {e}", exc_info=True) # ДІАГНОСТИКА

