import logging
from nodes import TriggerNode, BaseNode, Connection, RepeatNode, ConditionNodeZoneState

log = logging.getLogger(__name__)


class ScenarioSimulator:
    def __init__(self, scene, main_window):
        self.scene = scene
        self.main_window = main_window
        self.is_running = False
        self.current_nodes = []
        self.history = []
        self.loop_counters = {}  # Runtime state for loops {node_id: remaining_iterations}
        self._parent_map = {}  # Cache for parent lookups
        log.debug("ScenarioSimulator initialized.")

    def _build_parent_map(self):
        """Строит карту {child_id: parent_node} для эффективного обхода."""
        self._parent_map.clear()
        for item in self.scene.items():
            if isinstance(item, Connection):
                start_node = item.start_socket.parentItem()
                end_node = item.end_socket.parentItem()
                if start_node and end_node:
                    # У одного дочернего узла может быть много родителей (не в дереве, а в графе)
                    # Но для поиска цикла нам нужен только один входящий путь.
                    # ВАЖНО: Это предполагает, что у узла (кроме триггера) есть только ОДИН ВХОД.
                    # Это справедливо для нашей текущей логики (in_socket).
                    if end_node.in_socket and item.end_socket == end_node.in_socket:
                        self._parent_map[end_node.id] = start_node
        log.debug(f"Parent map built: {self._parent_map}")

    def _find_loop_parent(self, node):
        """Проходит вверх по пути выполнения, чтобы найти управляющий узел RepeatNode."""
        log.debug(f"Finding loop parent for node {node.id} ({node.node_name})")
        current_id = node.id
        visited = {current_id}
        while current_id in self._parent_map:
            parent = self._parent_map[current_id]
            log.debug(f"  -> Checking parent: {parent.id} ({parent.node_name})")
            if isinstance(parent, RepeatNode):
                # Если этот цикл активен (т.е. мы его запустили), возвращаем его
                if parent.id in self.loop_counters:
                    log.debug(f"  -> Found active loop parent: {parent.id}")
                    return parent
            current_id = parent.id
            if current_id in visited:  # Обнаружен цикл, прерываем
                log.warning(f"  -> Cycle detected in parent map lookup, breaking.")
                break
            visited.add(current_id)
        log.debug(f"  -> No active loop parent found for {node.id}")
        return None

    def start(self, trigger_zone_id):
        log.info(f"Attempting to start simulation with trigger zone: {trigger_zone_id}")
        if self.is_running:
            log.warning("Simulation start failed: Already running.")
            return False

        self.reset()
        self._build_parent_map()  # Строим карту в начале
        trigger_node = None
        for item in self.scene.items():
            if isinstance(item, TriggerNode):
                trigger_node = item
                break

        if not trigger_node:
            log.error("Simulation start failed: TriggerNode not found.")
            self.main_window.show_status_message("Помилка: Тригер не знайдено у сценарії.", 5000, color="red")
            return False

        trigger_props = dict(trigger_node.properties)
        if trigger_zone_id not in trigger_props.get('zones', []):
            log.warning(f"Simulation start failed: Trigger zone {trigger_zone_id} is not part of the trigger node.")
            self.main_window.show_status_message(f"Помилка: Вибрана зона не є частиною тригера.", 5000, color="orange")
            return False

        self.is_running = True
        self.current_nodes = [trigger_node]
        trigger_node.set_active_state(True)
        self.history.append(trigger_node)

        log.info(f"Simulation started successfully. Start node: {trigger_node.id}")
        self.main_window.show_status_message("Симуляцію розпочато. Натисніть 'Крок' для продовження.", color="lime")
        return True

    def step(self):
        if not self.is_running or not self.current_nodes:
            log.warning("Step called, but simulation is not running or no current nodes. Stopping.")
            self.stop()
            return

        log.debug(f"--- Simulation Step ---")
        log.debug(f"Current nodes: {[n.id for n in self.current_nodes]}")

        next_nodes_set = set()
        nodes_to_deactivate = list(self.current_nodes)
        connections_to_deactivate = []

        for node in self.current_nodes:
            log.debug(f"Processing node: {node.id} ({node.node_name})")
            # Визуально деактивируем входящие соединения
            if node.in_socket:
                for conn in node.in_socket.connections:
                    connections_to_deactivate.append(conn)

            # --- Логика узлов ---

            # 1. Узел "Повтор" (RepeatNode)
            if isinstance(node, RepeatNode):
                if node.id not in self.loop_counters:
                    # Инициализация счетчика при первом входе
                    props = dict(node.properties)
                    try:
                        count = int(props.get('count', 1))
                    except (ValueError, TypeError):
                        count = 1
                    self.loop_counters[node.id] = count if count != -1 else 1000  # 1000 for "infinite"
                    log.debug(f"  -> RepeatNode: Initialized counter to {self.loop_counters[node.id]}")

                if self.loop_counters[node.id] > 0:
                    # Есть итерации, выполняем тело цикла
                    self.loop_counters[node.id] -= 1
                    log.debug(f"  -> RepeatNode: Iteration remaining: {self.loop_counters[node.id]}. Following 'loop' path.")
                    if node.out_socket_loop and node.out_socket_loop.connections:
                        child_node = node.out_socket_loop.connections[0].end_socket.parentItem()
                        if isinstance(child_node, BaseNode):
                            next_nodes_set.add(child_node)
                    else:
                        log.warning(f"  -> RepeatNode: 'Loop' socket is not connected.")
                else:
                    # Итерации закончились, выходим из цикла
                    log.debug(f"  -> RepeatNode: Loop finished. Following 'end' path.")
                    if node.id in self.loop_counters:
                        del self.loop_counters[node.id]
                    if node.out_socket_end and node.out_socket_end.connections:
                        child_node = node.out_socket_end.connections[0].end_socket.parentItem()
                        if isinstance(child_node, BaseNode):
                            next_nodes_set.add(child_node)
                    else:
                        log.warning(f"  -> RepeatNode: 'End' socket is not connected.")

            # 2. Узел "Умова" (ConditionNodeZoneState)
            elif isinstance(node, ConditionNodeZoneState):
                user_choice = self.main_window.get_user_choice_for_condition(node)
                props = dict(node.properties)
                expected_state = props.get('state')
                log.debug(f"  -> ConditionNode: User choice='{user_choice}', Expected='{expected_state}'")

                if user_choice == expected_state:
                    # Условие выполнено
                    log.debug("  -> ConditionNode: Success. Following 'true' path.")
                    if node.out_socket_true and node.out_socket_true.connections:
                        child_node = node.out_socket_true.connections[0].end_socket.parentItem()
                        if isinstance(child_node, BaseNode):
                            next_nodes_set.add(child_node)
                    else:
                        log.warning(f"  -> ConditionNode: 'True' socket is not connected.")
                else:
                    # Условие не выполнено
                    log.debug("  -> ConditionNode: Failure. Following 'false' path.")
                    if node.out_socket_false and node.out_socket_false.connections:
                        child_node = node.out_socket_false.connections[0].end_socket.parentItem()
                        if isinstance(child_node, BaseNode):
                            next_nodes_set.add(child_node)
                    else:
                        log.warning(f"  -> ConditionNode: 'False' socket is not connected.")

            # 3. Стандартный узел (с одним out_socket)
            elif node.out_socket and node.out_socket.connections:
                log.debug(f"  -> StandardNode: Following 'out' path.")
                for conn in node.out_socket.connections:
                    child_node = conn.end_socket.parentItem()
                    if isinstance(child_node, BaseNode):
                        next_nodes_set.add(child_node)

            # 4. Конец ветки (нет исходящих соединений)
            elif not any(s.connections for s in node.get_output_sockets()):
                log.debug(f"  -> EndOfBranch: Node has no outgoing connections.")
                loop_parent = self._find_loop_parent(node)
                if loop_parent:
                    # Эта ветка закончилась, возвращаемся к узлу цикла
                    log.debug(f"  -> EndOfBranch: Returning to loop parent {loop_parent.id}")
                    next_nodes_set.add(loop_parent)
                else:
                    log.debug(f"  -> EndOfBranch: No loop parent, branch terminated.")
                    # Если родительского цикла нет, ветка просто заканчивается.

            # 5. Узел без out_socket (например, Decorator) или с отсоединенным выходом
            else:
                log.warning(
                    f"  -> Node {node.id} ({node.node_name}) was processed but has no logic path. "
                    f"It might be a logic dead-end or a node with disconnected outputs.")

        next_nodes = list(next_nodes_set)
        log.debug(f"Next nodes: {[n.id for n in next_nodes]}")

        # --- Визуальные обновления ---

        # Деактивируем ранее активные узлы и соединения
        for item in nodes_to_deactivate:
            item.set_active_state(False)
        for conn in connections_to_deactivate:
            conn.set_active_state(False)

        # Активируем новые узлы и ведущие к ним соединения
        for next_node in next_nodes:
            next_node.set_active_state(True)
            if next_node not in self.history:
                self.history.append(next_node)
            # Активируем соединение
            if next_node.in_socket:
                for conn in next_node.in_socket.connections:
                    # Проверяем, что родительский узел был активен на этом шаге
                    if conn.start_socket.parentItem() in self.current_nodes:
                        conn.set_active_state(True)

        self.current_nodes = next_nodes

        if not self.current_nodes:
            self.is_running = False
            log.info("Simulation finished: No more nodes to process.")
            self.main_window.show_status_message("Симуляція завершена.", color="lime")
            # Явно вызываем stop() для сброса состояния UI
            self.main_window.stop_simulation()

    def stop(self):
        log.info("Simulation stop called.")
        if not self.is_running and not self.history:
            log.debug("Stop called, but simulation was not running and history is empty. No action taken.")
            return
        self.reset()
        self.main_window.show_status_message("Симуляцію зупинено.")

    def reset(self):
        log.debug("Resetting simulation state.")
        self.is_running = False
        self.current_nodes = []
        self.history = []
        self.loop_counters.clear()
        self._parent_map.clear()  # Карта родителей очищается при сбросе
        for item in self.scene.items():
            if isinstance(item, (BaseNode, Connection)):
                item.set_active_state(False)