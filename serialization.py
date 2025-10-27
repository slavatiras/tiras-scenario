# -*- coding: utf-8 -*-
import logging
from lxml import etree as ET
from PyQt6.QtWidgets import QMessageBox # Потрібен для повідомлень про помилки

# Імпортуємо визначення вузлів та з'єднань
from nodes import BaseNode, Connection, CommentItem, FrameItem

log = logging.getLogger(__name__)

def import_project_data(path):
    """
    Завантажує дані проекту з XML-файлу за вказаним шляхом.
    Повертає словник з даними проекту або None у разі помилки.
    """
    log.info(f"Attempting to load project data from: {path}")
    try:
        root_xml = ET.parse(path).getroot()
        new_project_data = {
            'scenarios': {},
            'macros': {},
            'config': {'devices': [], 'users': []}
        }

        # Config
        config_xml = root_xml.find("config")
        if config_xml is not None:
            log.debug("Parsing <config> section...")
            devices_xml = config_xml.find("devices")
            if devices_xml is not None:
                log.debug("Parsing <devices>...")
                for device_el in devices_xml:
                    device_id = device_el.get('id')
                    log.debug(f"Parsing device ID: {device_id}")
                    device_data = {'id': device_id, 'name': device_el.get('name'),
                                   'type': device_el.get('type'), 'zones': [], 'outputs': []}
                    zones_xml = device_el.find('zones')
                    if zones_xml is not None:
                        for zone_el in zones_xml:
                            log.debug(f"  - Parsing zone ID: {zone_el.get('id')}")
                            device_data['zones'].append(
                                {'id': zone_el.get('id'), 'name': zone_el.get('name'),
                                 'parent_name': device_data['name']}) # Додаємо parent_name одразу
                    outputs_xml = device_el.find('outputs')
                    if outputs_xml is not None:
                        for output_el in outputs_xml:
                            log.debug(f"  - Parsing output ID: {output_el.get('id')}")
                            device_data['outputs'].append(
                                {'id': output_el.get('id'), 'name': output_el.get('name'),
                                 'parent_name': device_data['name']}) # Додаємо parent_name одразу
                    new_project_data['config']['devices'].append(device_data)

            users_xml = config_xml.find("users")
            if users_xml is not None:
                log.debug("Parsing <users>...")
                for user_el in users_xml:
                    log.debug(f"Parsing user ID: {user_el.get('id')}")
                    new_project_data['config']['users'].append(
                        {'id': user_el.get("id"), 'name': user_el.get("name"), 'phone': user_el.get("phone")})

        # Scenarios
        scenarios_xml = root_xml.find("scenarios")
        if scenarios_xml is not None:
            log.debug("Parsing <scenarios> section...")
            for scenario_el in scenarios_xml:
                scenario_id = scenario_el.get("id")
                log.debug(f"Parsing scenario ID: {scenario_id}")
                if not scenario_id: continue
                nodes_data, connections_data, comments_data, frames_data = [], [], [], []

                nodes_xml = scenario_el.find("nodes")
                if nodes_xml is not None:
                    for node_el in nodes_xml:
                        log.debug(f"  - Parsing node ID: {node_el.get('id')}")
                        nodes_data.append(BaseNode.data_from_xml(node_el))

                connections_xml = scenario_el.find("connections")
                if connections_xml is not None:
                    for conn_el in connections_xml:
                        log.debug(
                            f"  - Parsing connection from: {conn_el.get('from_node')} to: {conn_el.get('to_node')}")
                        connections_data.append(Connection.data_from_xml(conn_el))

                comments_xml = scenario_el.find("comments")
                if comments_xml is not None:
                    for comment_el in comments_xml:
                        log.debug(f"  - Parsing comment ID: {comment_el.get('id')}")
                        comments_data.append(CommentItem.data_from_xml(comment_el))

                frames_xml = scenario_el.find("frames")
                if frames_xml is not None:
                    for frame_el in frames_xml:
                        log.debug(f"  - Parsing frame ID: {frame_el.get('id')}")
                        frames_data.append(FrameItem.data_from_xml(frame_el))

                new_project_data['scenarios'][scenario_id] = {'nodes': nodes_data, 'connections': connections_data,
                                                              'comments': comments_data, 'frames': frames_data}

        # Macros
        macros_xml = root_xml.find("macros")
        if macros_xml is not None:
            log.debug("Parsing <macros> section...")
            for macro_el in macros_xml:
                macro_id = macro_el.get("id")
                log.debug(f"Parsing macro ID: {macro_id}")
                if not macro_id: continue
                macro_data = {
                    'id': macro_id,
                    'name': macro_el.get('name'),
                    'nodes': [], 'connections': [], 'inputs': [], 'outputs': [],
                    # Додаємо підтримку коментарів та фреймів у макросах при імпорті
                    'comments': [], 'frames': []
                }
                nodes_xml = macro_el.find("nodes")
                if nodes_xml is not None:
                    for node_el in nodes_xml: macro_data['nodes'].append(BaseNode.data_from_xml(node_el))
                connections_xml = macro_el.find("connections")
                if connections_xml is not None:
                    for conn_el in connections_xml: macro_data['connections'].append(
                        Connection.data_from_xml(conn_el))
                comments_xml = macro_el.find("comments")
                if comments_xml is not None:
                    for comment_el in comments_xml: macro_data['comments'].append(CommentItem.data_from_xml(comment_el))
                frames_xml = macro_el.find("frames")
                if frames_xml is not None:
                    for frame_el in frames_xml: macro_data['frames'].append(FrameItem.data_from_xml(frame_el))


                # Parse inputs/outputs definitions
                inputs_xml = macro_el.find("inputs")
                if inputs_xml is not None:
                    for input_el in inputs_xml:
                        macro_data['inputs'].append({
                            'name': input_el.get('name'),
                            'macro_input_node_id': input_el.get('node_id')
                        })
                outputs_xml = macro_el.find("outputs")
                if outputs_xml is not None:
                    for output_el in outputs_xml:
                        macro_data['outputs'].append({
                            'name': output_el.get('name'),
                            'macro_output_node_id': output_el.get('node_id')
                        })

                new_project_data['macros'][macro_id] = macro_data

        log.debug("Successfully finished parsing XML file.")
        return new_project_data
    except ET.XMLSyntaxError as e:
        log.critical(f"XML Syntax error while parsing file: {e}", exc_info=True)
        QMessageBox.critical(None, "Помилка читання файлу", f"Помилка синтаксису XML у файлі:\n{e}")
        return None
    except FileNotFoundError:
        log.critical(f"File not found during import: {path}", exc_info=True)
        QMessageBox.critical(None, "Помилка читання файлу", f"Файл не знайдено:\n{path}")
        return None
    except Exception as e:
        log.critical(f"Critical error while parsing XML file: {e}", exc_info=True)
        QMessageBox.critical(None, "Помилка читання файлу", f"Не вдалося прочитати дані з файлу:\n{e}")
        return None


def export_project_data(path, project_data):
    """
    Зберігає дані проекту (project_data) у XML-файл за вказаним шляхом.
    Повертає True у разі успіху, False у разі помилки.
    """
    log.info(f"Exporting project data to: {path}")
    if not project_data:
        log.error("Export failed: Project data is empty.")
        return False
    try:
        root_xml = ET.Element("project")

        # Config saving
        config_xml = ET.SubElement(root_xml, "config")
        devices_xml = ET.SubElement(config_xml, "devices")
        for device in project_data.get('config', {}).get('devices', []):
            device_el = ET.SubElement(devices_xml, "device", id=str(device.get('id','')),
                                      name=str(device.get('name','')), type=str(device.get('type','')))
            zones_xml = ET.SubElement(device_el, 'zones')
            outputs_xml = ET.SubElement(device_el, 'outputs')
            for zone in device.get('zones', []):
                ET.SubElement(zones_xml, 'zone', id=str(zone.get('id','')), name=str(zone.get('name','')))
            for output in device.get('outputs', []):
                ET.SubElement(outputs_xml, 'output', id=str(output.get('id','')), name=str(output.get('name','')))
        users_xml = ET.SubElement(config_xml, "users")
        for user in project_data.get('config', {}).get('users', []):
            ET.SubElement(users_xml, "user", id=str(user.get('id','')), name=str(user.get('name','')),
                          phone=str(user.get('phone', '')))

        # Scenarios saving
        scenarios_xml = ET.SubElement(root_xml, "scenarios")
        for scenario_id, scenario_data in project_data.get('scenarios', {}).items():
            scenario_el = ET.SubElement(scenarios_xml, "scenario", id=str(scenario_id))
            nodes_el = ET.SubElement(scenario_el, "nodes")
            conns_el = ET.SubElement(scenario_el, "connections")
            comms_el = ET.SubElement(scenario_el, "comments")
            frames_el = ET.SubElement(scenario_el, "frames")
            for node_data in scenario_data.get('nodes', []): BaseNode.data_to_xml(nodes_el, node_data)
            for conn_data in scenario_data.get('connections', []): Connection.data_to_xml(conns_el, conn_data)
            for comm_data in scenario_data.get('comments', []): CommentItem.data_to_xml(comms_el, comm_data)
            for frame_data in scenario_data.get('frames', []): FrameItem.data_to_xml(frames_el, frame_data)

        # Macros saving
        macros_xml = ET.SubElement(root_xml, "macros")
        for macro_id, macro_data in project_data.get('macros', {}).items():
            macro_el = ET.SubElement(macros_xml, "macro", id=str(macro_id), name=str(macro_data.get('name', '')))
            nodes_el = ET.SubElement(macro_el, "nodes")
            conns_el = ET.SubElement(macro_el, "connections")
            inputs_el = ET.SubElement(macro_el, "inputs")
            outputs_el = ET.SubElement(macro_el, "outputs")
            # Додаємо збереження коментарів та фреймів у макросах
            comms_el = ET.SubElement(macro_el, "comments")
            frames_el = ET.SubElement(macro_el, "frames")

            for node_data in macro_data.get('nodes', []): BaseNode.data_to_xml(nodes_el, node_data)
            for conn_data in macro_data.get('connections', []): Connection.data_to_xml(conns_el, conn_data)
            for comm_data in macro_data.get('comments', []): CommentItem.data_to_xml(comms_el, comm_data)
            for frame_data in macro_data.get('frames', []): FrameItem.data_to_xml(frames_el, frame_data)

            for input_data in macro_data.get('inputs', []):
                ET.SubElement(inputs_el, "input", name=str(input_data.get('name', '')),
                              node_id=str(input_data.get('macro_input_node_id', '')))
            for output_data in macro_data.get('outputs', []):
                ET.SubElement(outputs_el, "output", name=str(output_data.get('name', '')),
                              node_id=str(output_data.get('macro_output_node_id', '')))

        tree = ET.ElementTree(root_xml)
        tree.write(path, pretty_print=True, xml_declaration=True, encoding="utf-8")
        log.info(f"Project data successfully exported to {path}")
        return True
    except Exception as e:
        log.error(f"Failed to export project data: {e}", exc_info=True)
        QMessageBox.critical(None, "Помилка експорту", f"Не вдалося експортувати проект:\n{e}")
        return False
