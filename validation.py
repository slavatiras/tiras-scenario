# -*- coding: utf-8 -*-
import logging
from PyQt6.QtCore import QTimer # Потрібен для QTimer.singleShot

# Імпортуємо необхідні типи вузлів
from nodes import (BaseNode, TriggerNode, ActivateOutputNode, DeactivateOutputNode,
                   SendSMSNode, MacroInputNode, MacroOutputNode, MacroNode, Connection)
from constants import EDIT_MODE_SCENARIO, EDIT_MODE_MACRO # Потрібні для визначення режиму

log = logging.getLogger(__name__)

# --- Функції валідації сценарію ---

def validate_scenario_on_scene(scene, config):
    """
    Запускає валідацію сценарію на вказаній сцені з невеликою затримкою.
    """
    if not scene:
        log.warning("validate_scenario_on_scene: Scene is None.")
        return
    # Використовуємо singleShot для уникнення проблем з оновленням UI
    QTimer.singleShot(1, lambda: _perform_scenario_validation(scene, config))

def _perform_scenario_validation(scene, config):
    """
    Виконує детальну валідацію сценарію на сцені.
    """
    log.debug("Starting scenario validation...")
    if not scene:
        log.warning("_perform_scenario_validation: Scene is None.")
        return

    all_nodes = []
    trigger_node = None

    # 1. Базова валідація кожного вузла
    log.debug("Step 1: Validating individual nodes...")
    items_on_scene = scene.items() # Отримуємо список один раз
    for item in items_on_scene:
        if isinstance(item, BaseNode):
            all_nodes.append(item)
            try:
                # Спочатку скидаємо старі помилки валідації, пов'язані з логікою (недосяжність, незавершеність)
                current_tooltip = item.error_icon.toolTip()
                if current_tooltip in ["Вузол недосяжний від тригера.", "Ланцюжок логіки не завершено дією."]:
                     item.set_validation_state(True) # Скидаємо, якщо немає інших помилок

                # Викликаємо індивідуальну валідацію вузла
                item.validate(config)
                log.debug(f"  Node {item.id} ({item.node_type}) validated. Error visible: {item.error_icon.isVisible()}")
            except Exception as e:
                log.error(f"  Error validating node {item.id}: {e}", exc_info=True)
                item.set_validation_state(False, f"Помилка валідації: {e}") # Позначаємо вузол як невалідний

            if isinstance(item, TriggerNode):
                if trigger_node is None:
                    trigger_node = item
                else:
                     log.warning("Multiple TriggerNodes found!") # Хоча логіка додавання це запобігає
                     item.set_validation_state(False, "У сценарії може бути лише один тригер.")

    # 2. Перевірка наявності та валідності тригера
    log.debug("Step 2: Checking trigger node...")
    if not trigger_node:
        log.warning("Scenario validation failed: TriggerNode not found.")
        # Позначаємо всі інші вузли (якщо вони ще не мають помилок)
        for node in all_nodes:
            if not isinstance(node, TriggerNode) and not node.error_icon.isVisible():
                node.set_validation_state(False, "В сценарії відсутній тригер.")
        return # Подальша перевірка неможлива

    if trigger_node.error_icon.isVisible():
        log.warning(f"Scenario validation stopped: TriggerNode {trigger_node.id} is invalid.")
        return # Якщо сам тригер невалідний, перевірка досяжності не має сенсу

    # 3. Перевірка досяжності вузлів від тригера
    log.debug("Step 3: Checking node reachability from trigger...")
    reachable_nodes = {trigger_node}
    queue = [trigger_node]
    processed_connections = set() # Для уникнення зациклення в складних графах

    while queue:
        current_node = queue.pop(0)
        log.debug(f"  Checking reachability from: {current_node.id}")
        for socket in current_node.get_output_sockets():
            for conn in socket.connections:
                 # Перевірка валідності з'єднання та цільового вузла
                 if conn in processed_connections or not conn.end_socket or not conn.end_socket.parentItem():
                      # log.debug(f"    Skipping connection {conn} (processed or invalid end)")
                      continue

                 next_node = conn.end_socket.parentItem()
                 if isinstance(next_node, BaseNode) and next_node not in reachable_nodes:
                     log.debug(f"    Node {next_node.id} is reachable.")
                     reachable_nodes.add(next_node)
                     queue.append(next_node)
                 processed_connections.add(conn)


    # 4. Позначення недосяжних вузлів та перевірка незавершених ланцюжків
    log.debug("Step 4: Marking unreachable nodes and checking unterminated branches...")
    TERMINAL_NODE_TYPES = (ActivateOutputNode, DeactivateOutputNode, SendSMSNode)

    for node in all_nodes:
        is_reachable = node in reachable_nodes
        is_terminal = isinstance(node, TERMINAL_NODE_TYPES)
        # MacroNode вважається термінальним, якщо у нього немає виходів АБО всі виходи підключені
        is_macro_terminal = isinstance(node, MacroNode) and not node.get_output_sockets()

        has_connected_outputs = any(sock.connections for sock in node.get_output_sockets())

        if not is_reachable:
            # Недосяжні вузли - це завжди помилка (крім самого тригера, якщо щось пішло не так)
            if node is not trigger_node:
                 log.warning(f"  Node {node.id} is unreachable.")
                 node.set_validation_state(False, "Вузол недосяжний від тригера.")
        elif not is_terminal and not is_macro_terminal and not has_connected_outputs:
             # Досяжний, але не термінальний і не має вихідних з'єднань
             log.warning(f"  Node {node.id} is reachable but has no connected outputs (and is not terminal).")
             # Показуємо помилку тільки якщо немає іншої помилки від validate()
             if not node.error_icon.isVisible():
                  node.set_validation_state(False, "Ланцюжок логіки не завершено дією.")
        # else: # Вузол досяжний і або термінальний, або має виходи
        #      # Якщо раніше була помилка про незавершеність, а тепер все ОК, скидаємо її
        #      if node.error_icon.toolTip() == "Ланцюжок логіки не завершено дією." and not node.error_icon.isVisible():
        #           log.debug(f"  Clearing 'unterminated branch' error for node {node.id}")
        #           node.set_validation_state(True) # Скидаємо ТІЛЬКИ ЦЮ помилку


    log.debug("Scenario validation finished.")


# --- Функції валідації макросу ---

def validate_macro_on_scene(scene, config):
    """
    Запускає валідацію макросу на вказаній сцені з невеликою затримкою.
    """
    if not scene:
        log.warning("validate_macro_on_scene: Scene is None.")
        return
    QTimer.singleShot(1, lambda: _perform_macro_validation(scene, config))

def _perform_macro_validation(scene, config):
    """
    Виконує детальну валідацію макросу на сцені.
    """
    log.debug("Starting macro validation...")
    if not scene:
        log.warning("_perform_macro_validation: Scene is None.")
        return

    all_nodes = []
    input_nodes = []
    output_nodes = []

    # 1. Базова валідація та збір вузлів входу/виходу
    log.debug("Step 1: Validating individual nodes and collecting IO nodes...")
    items_on_scene = scene.items()
    for item in items_on_scene:
        if isinstance(item, BaseNode):
            all_nodes.append(item)
            try:
                # Скидаємо старі помилки, специфічні для макро-валідації
                current_tooltip = item.error_icon.toolTip()
                macro_specific_errors = [
                     "Ім'я входу", "Ім'я виходу", # Частина повідомлення про дублікат
                     "нікуди не підключено", # Вхід не підключено
                     "нічого не підключено" # До виходу нічого не підключено
                ]
                # Перевіряємо, чи починається tooltip з однієї з фраз
                if any(current_tooltip.startswith(err) for err in macro_specific_errors):
                     item.set_validation_state(True) # Скидаємо, якщо немає інших помилок

                item.validate(config) # Індивідуальна валідація
                log.debug(f"  Node {item.id} ({item.node_type}) validated. Error visible: {item.error_icon.isVisible()}")
            except Exception as e:
                log.error(f"  Error validating node {item.id}: {e}", exc_info=True)
                item.set_validation_state(False, f"Помилка валідації: {e}")

            if isinstance(item, MacroInputNode):
                input_nodes.append(item)
            elif isinstance(item, MacroOutputNode):
                output_nodes.append(item)

    # 2. Перевірка унікальності імен входів/виходів
    log.debug("Step 2: Checking uniqueness of IO node names...")
    input_names = [n.node_name for n in input_nodes]
    output_names = [n.node_name for n in output_nodes]
    input_name_counts = {name: input_names.count(name) for name in set(input_names)}
    output_name_counts = {name: output_names.count(name) for name in set(output_names)}

    log.debug(f"  Input name counts: {input_name_counts}")
    log.debug(f"  Output name counts: {output_name_counts}")

    duplicate_found = False
    for node in input_nodes:
        if input_name_counts.get(node.node_name, 1) > 1:
            log.warning(f"  Duplicate input name found: {node.node_name} (Node ID: {node.id})")
            node.set_validation_state(False, f"Ім'я входу '{node.node_name}' не є унікальним.")
            duplicate_found = True

    for node in output_nodes:
        if output_name_counts.get(node.node_name, 1) > 1:
            log.warning(f"  Duplicate output name found: {node.node_name} (Node ID: {node.id})")
            node.set_validation_state(False, f"Ім'я виходу '{node.node_name}' не є унікальним.")
            duplicate_found = True

    if duplicate_found:
         log.error("Macro validation failed: Duplicate IO node names detected.")
         # Можна зупинити подальшу валідацію тут, якщо потрібно

    # 3. Перевірка підключення входів/виходів
    log.debug("Step 3: Checking IO node connections...")
    for inp_node in input_nodes:
        # Вхід повинен мати вихідний сокет і хоча б одне з'єднання з нього
        if not inp_node.out_socket or not inp_node.out_socket.connections:
            if not inp_node.error_icon.isVisible(): # Не перезаписуємо інші помилки
                log.warning(f"  Input node '{inp_node.node_name}' (ID: {inp_node.id}) has no outgoing connection.")
                inp_node.set_validation_state(False, f"Вхід '{inp_node.node_name}' нікуди не підключено.")

    for outp_node in output_nodes:
        # Вихід повинен мати вхідний сокет і хоча б одне з'єднання до нього
        if not outp_node.in_socket or not outp_node.in_socket.connections:
            if not outp_node.error_icon.isVisible(): # Не перезаписуємо інші помилки
                log.warning(f"  Output node '{outp_node.node_name}' (ID: {outp_node.id}) has no incoming connection.")
                outp_node.set_validation_state(False, f"До виходу '{outp_node.node_name}' нічого не підключено.")

    # 4. (Опціонально) Перевірка досяжності всіх виходів від усіх входів
    # log.debug("Step 4: Checking reachability from inputs to outputs (optional)...")
    # Ця перевірка складніша і може бути додана пізніше, якщо буде потрібна.
    # Вона вимагає обходу графа від кожного входу.

    log.debug("Macro validation finished.")
