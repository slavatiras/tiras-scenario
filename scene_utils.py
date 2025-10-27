# -*- coding: utf-8 -*-
import logging
from PyQt6.QtCore import QPointF

# Імпортуємо всі типи вузлів та елементів
from nodes import (BaseNode, Connection, CommentItem, FrameItem, MacroNode)

log = logging.getLogger(__name__)

def populate_scene_from_data(scene, data, view, macros_data=None):
    """
    Заповнює сцену графічними елементами з наданих даних.
    'data' - це словник, що містить 'nodes', 'connections', 'comments', 'frames'.
    'macros_data' - словник з визначеннями макросів (потрібен для MacroNode).
    """
    if not scene or not data:
        log.warning("populate_scene_from_data: Scene or data is missing.")
        return

    log.debug(f"Populating scene with data (has {len(data.get('nodes', []))} nodes, {len(data.get('connections', []))} connections)")
    nodes_map = {}
    items_added = 0

    # 1. Створюємо вузли, коментарі, фрейми
    for node_data in data.get('nodes', []):
        try:
            node = BaseNode.from_data(node_data)
            # Оновлюємо сокети для MacroNode, якщо є визначення
            if isinstance(node, MacroNode) and node.macro_id and macros_data:
                macro_def = macros_data.get(node.macro_id)
                if macro_def:
                    log.debug(f"    Updating sockets for MacroNode {node.id} from definition {node.macro_id}.")
                    node.update_sockets_from_definition(macro_def)
                else:
                    log.warning(f"    Macro definition {node.macro_id} not found for MacroNode {node.id}.")
            scene.addItem(node)
            nodes_map[node.id] = node
            items_added += 1
        except Exception as e:
            log.error(f"Failed to create/add node from data {node_data}: {e}", exc_info=True)

    for comment_data in data.get('comments', []):
        try:
            # Переконуємось, що розміри - це числа
            width = float(comment_data.get('size', [200, 100])[0])
            height = float(comment_data.get('size', [200, 100])[1])
            comment = CommentItem(comment_data.get('text', ''), width, height, view)
            comment.id = comment_data.get('id') # Встановлюємо ID з даних
            comment.setPos(QPointF(*comment_data.get('pos', (0,0))))
            comment.resize_handle.setVisible(False)
            scene.addItem(comment)
            items_added += 1
        except Exception as e:
            log.error(f"Failed to create/add comment from data {comment_data}: {e}", exc_info=True)

    for frame_data in data.get('frames', []):
        try:
            # Переконуємось, що розміри - це числа
            width = float(frame_data.get('size', [300, 200])[0])
            height = float(frame_data.get('size', [300, 200])[1])
            frame = FrameItem(frame_data.get('text', ''), width, height, view)
            frame.id = frame_data.get('id') # Встановлюємо ID з даних
            frame.setPos(QPointF(*frame_data.get('pos', (0,0))))
            frame.resize_handle.setVisible(False)
            scene.addItem(frame)
            items_added += 1
        except Exception as e:
            log.error(f"Failed to create/add frame from data {frame_data}: {e}", exc_info=True)

    # 2. Створюємо з'єднання
    for conn_data in data.get('connections', []):
        start_node = nodes_map.get(conn_data['from_node'])
        end_node = nodes_map.get(conn_data['to_node'])
        if start_node and end_node:
            start_socket = start_node.get_socket(conn_data.get('from_socket', 'out'))
            end_socket = end_node.get_socket(conn_data.get('to_socket', 'in'))
            if start_socket and end_socket:
                try:
                    conn = Connection(start_socket, end_socket)
                    scene.addItem(conn)
                    items_added += 1
                except Exception as e:
                    log.error(f"Failed to create/add connection from data {conn_data}: {e}", exc_info=True)

            else:
                log.warning(f"Could not create connection, socket not found for data: {conn_data}")
        else:
            log.warning(f"Could not create connection, node not found for data: {conn_data}")

    log.debug(f"Populated scene with a total of {items_added} items.")


def extract_data_from_scene(scene):
    """
    Витягує дані про елементи (вузли, з'єднання, коментарі, фрейми) зі сцени
    у форматі словника.
    """
    if not scene:
        log.warning("extract_data_from_scene: Scene is None.")
        return {'nodes': [], 'connections': [], 'comments': [], 'frames': []}

    scene_data = {'nodes': [], 'connections': [], 'comments': [], 'frames': []}
    items_processed = 0
    items_total = len(scene.items())

    log.debug(f"Extracting data from scene with {items_total} items...")

    for item in scene.items():
        data = None
        try:
            if isinstance(item, BaseNode):
                data = item.to_data()
                if data: scene_data['nodes'].append(data)
            elif isinstance(item, Connection):
                data = item.to_data()
                 # Додаємо з'єднання тільки якщо воно валідне (має обидва кінці)
                if data and data.get('from_node') and data.get('to_node'):
                    scene_data['connections'].append(data)
                elif data:
                     log.warning(f"Skipping extraction of invalid connection data: {data} (Item: {item})")
            elif isinstance(item, CommentItem):
                data = item.to_data()
                if data: scene_data['comments'].append(data)
            elif isinstance(item, FrameItem):
                data = item.to_data()
                if data: scene_data['frames'].append(data)

            if data: items_processed += 1

        except Exception as e:
            log.error(f"Error extracting data from item {item}: {e}", exc_info=True)

    log.debug(f"Extracted data for {items_processed} items. "
              f"Nodes: {len(scene_data['nodes'])}, "
              f"Connections: {len(scene_data['connections'])}, "
              f"Comments: {len(scene_data['comments'])}, "
              f"Frames: {len(scene_data['frames'])}")
    return scene_data
