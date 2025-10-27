# -*- coding: utf-8 -*-
import logging
from lxml import etree as ET
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QPointF

# Імпортуємо необхідні типи елементів та команду вставки
from nodes import BaseNode, Connection, CommentItem, FrameItem
from commands import PasteCommand # Команда вставки використовується тут

log = logging.getLogger(__name__)

def copy_selection_to_clipboard(scene):
    """
    Копіює виділені елементи сцени (вузли, коментарі, фрейми та з'єднання між ними)
    у буфер обміну у форматі XML.
    Повертає кількість скопійованих основних елементів (вузли+коментарі+фрейми).
    """
    if not scene:
        log.warning("copy_selection_to_clipboard: Scene is None.")
        return 0

    selected_items = scene.selectedItems()
    nodes_to_copy = [item for item in selected_items if isinstance(item, BaseNode)]
    comments_to_copy = [item for item in selected_items if isinstance(item, CommentItem)]
    frames_to_copy = [item for item in selected_items if isinstance(item, FrameItem)]

    copied_count = len(nodes_to_copy) + len(comments_to_copy) + len(frames_to_copy)
    if copied_count == 0:
        log.debug("copy_selection_to_clipboard: Nothing selected to copy.")
        return 0

    clipboard_root = ET.Element("clipboard_data")
    nodes_xml = ET.SubElement(clipboard_root, "nodes")
    connections_xml = ET.SubElement(clipboard_root, "connections")
    comments_xml = ET.SubElement(clipboard_root, "comments")
    frames_xml = ET.SubElement(clipboard_root, "frames")

    node_ids_to_copy = {node.id for node in nodes_to_copy}

    log.debug(f"Copying {len(nodes_to_copy)} nodes, {len(comments_to_copy)} comments, {len(frames_to_copy)} frames.")

    for node in nodes_to_copy:
        try:
            node.to_xml(nodes_xml)
        except Exception as e:
            log.error(f"Error serializing node {node.id} for copy: {e}", exc_info=True)
    for comment in comments_to_copy:
        try:
            comment.to_xml(comments_xml)
        except Exception as e:
            log.error(f"Error serializing comment {comment.id} for copy: {e}", exc_info=True)
    for frame in frames_to_copy:
        try:
            frame.to_xml(frames_xml)
        except Exception as e:
            log.error(f"Error serializing frame {frame.id} for copy: {e}", exc_info=True)


    # Копіюємо тільки ті з'єднання, обидва кінці яких серед виділених вузлів
    copied_connections = 0
    all_connections = [item for item in scene.items() if isinstance(item, Connection)]
    for item in all_connections:
        start_node = item.start_socket.parentItem() if item.start_socket else None
        end_node = item.end_socket.parentItem() if item.end_socket else None
        if start_node and end_node and start_node.id in node_ids_to_copy and end_node.id in node_ids_to_copy:
            try:
                item.to_xml(connections_xml)
                copied_connections += 1
            except Exception as e:
                log.error(f"Error serializing connection for copy (Start: {start_node.id}, End: {end_node.id}): {e}", exc_info=True)

    log.debug(f"Copied {copied_connections} internal connections.")

    try:
        clipboard_string = ET.tostring(clipboard_root, pretty_print=True, encoding="unicode")
        QApplication.clipboard().setText(clipboard_string)
        log.info(f"Copied {copied_count} elements (+ {copied_connections} connections) to clipboard.")
        return copied_count
    except Exception as e:
        log.error(f"Error writing to clipboard: {e}", exc_info=True)
        return 0


def paste_selection_from_clipboard(scene, paste_pos: QPointF, view, current_edit_mode, undo_stack):
    """
    Зчитує дані з буфера обміну та створює команду PasteCommand для вставки елементів
    на сцену в зазначеній позиції.
    Повертає True, якщо команда була створена та додана до стеку, інакше False.
    """
    clipboard_string = QApplication.clipboard().text()
    if not clipboard_string:
        log.debug("paste_selection_from_clipboard: Clipboard is empty.")
        return False

    if not scene:
        log.error("paste_selection_from_clipboard: Scene is None.")
        return False

    try:
        # Перевірка, чи XML валідний (без повного парсингу тут)
        ET.fromstring(clipboard_string.encode('utf-8'))
        log.debug("Clipboard contains valid XML.")
        # Створюємо та додаємо команду
        command = PasteCommand(scene, clipboard_string, paste_pos, view, current_edit_mode)
        undo_stack.push(command)
        return True
    except ET.XMLSyntaxError:
        log.warning("paste_selection_from_clipboard: Clipboard does not contain valid XML.")
        return False
    except Exception as e:
        log.error(f"paste_selection_from_clipboard: Error creating PasteCommand: {e}", exc_info=True)
        return False
