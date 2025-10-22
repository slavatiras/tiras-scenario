from nodes import TriggerNode, BaseNode, Connection, RepeatNode


class ScenarioSimulator:
    def __init__(self, scene, main_window):
        self.scene = scene
        self.main_window = main_window
        self.is_running = False
        self.current_nodes = []
        self.history = []
        self.loop_counters = {}  # Runtime state for loops {node_id: remaining_iterations}
        self._parent_map = {}  # Cache for parent lookups

    def _build_parent_map(self):
        """Строит карту {child_id: parent_node} для эффективного обхода."""
        self._parent_map.clear()
        for item in self.scene.items():
            if isinstance(item, Connection):
                start_node = item.start_socket.parentItem()
                end_node = item.end_socket.parentItem()
                if start_node and end_node:
                    self._parent_map[end_node.id] = start_node

    def _find_loop_parent(self, node):
        """Проходит вверх по пути выполнения, чтобы найти управляющий узел RepeatNode."""
        current_id = node.id
        visited = {current_id}
        while current_id in self._parent_map:
            parent = self._parent_map[current_id]
            if isinstance(parent, RepeatNode):
                # Если этот цикл активен, возвращаем его
                if parent.id in self.loop_counters:
                    return parent
            current_id = parent.id
            if current_id in visited:  # Обнаружен цикл, прерываем
                break
            visited.add(current_id)
        return None

    def start(self, trigger_zone_id):
        if self.is_running:
            return False

        self.reset()
        self._build_parent_map()  # Строим карту в начале
        trigger_node = None
        for item in self.scene.items():
            if isinstance(item, TriggerNode):
                trigger_node = item
                break

        if not trigger_node:
            self.main_window.show_status_message("Помилка: Тригер не знайдено у сценарії.", 5000, color="red")
            return False

        trigger_props = dict(trigger_node.properties)
        if trigger_zone_id not in trigger_props.get('zones', []):
            self.main_window.show_status_message(f"Помилка: Вибрана зона не є частиною тригера.", 5000, color="orange")
            return False

        self.is_running = True
        self.current_nodes = [trigger_node]
        trigger_node.set_active_state(True)
        self.history.append(trigger_node)

        self.main_window.show_status_message("Симуляцію розпочато. Натисніть 'Крок' для продовження.", color="lime")
        return True

    def step(self):
        if not self.is_running or not self.current_nodes:
            self.stop()
            return

        next_nodes_set = set()
        nodes_to_deactivate = list(self.current_nodes)
        connections_to_deactivate = []

        for node in self.current_nodes:
            # Визуально деактивируем входящие соединения
            if node.in_socket:
                for conn in node.in_socket.connections:
                    connections_to_deactivate.append(conn)

            # --- Логика узлов ---

            # 1. Обработка входа в RepeatNode
            if isinstance(node, RepeatNode):
                if node.id not in self.loop_counters:
                    props = dict(node.properties)
                    try:
                        count = int(props.get('count', 1))
                    except (ValueError, TypeError):
                        count = 1  # Использовать значение по умолчанию при ошибке
                    # Для бесконечных циклов (-1) используем большое число для безопасности.
                    self.loop_counters[node.id] = count if count != -1 else 1000

                if self.loop_counters[node.id] > 0:
                    self.loop_counters[node.id] -= 1
                    # Переходим к дочернему узлу (началу тела цикла)
                    if node.out_socket and node.out_socket.connections:
                        child_node = node.out_socket.connections[0].end_socket.parentItem()
                        if isinstance(child_node, BaseNode):
                            next_nodes_set.add(child_node)
                else:
                    # Цикл завершен, очищаем счетчик и завершаем эту ветку
                    if node.id in self.loop_counters:
                        del self.loop_counters[node.id]

            # 2. Обработка конца ветки (проверка на цикл)
            elif not (node.out_socket and node.out_socket.connections):
                loop_parent = self._find_loop_parent(node)
                if loop_parent:
                    # Эта ветка закончилась, возвращаемся к узлу цикла для проверки следующей итерации
                    next_nodes_set.add(loop_parent)
                # Если родительского цикла нет, ветка просто заканчивается.

            # 3. Стандартный обход узла
            else:
                if node.out_socket:
                    for conn in node.out_socket.connections:
                        child_node = conn.end_socket.parentItem()
                        if isinstance(child_node, BaseNode):
                            next_nodes_set.add(child_node)

        next_nodes = list(next_nodes_set)
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
            self.main_window.show_status_message("Симуляція завершена.", color="lime")


    def stop(self):
        if not self.is_running and not self.history:
            return
        self.reset()
        self.main_window.show_status_message("Симуляцію зупинено.")

    def reset(self):
        self.is_running = False
        self.current_nodes = []
        self.history = []
        self.loop_counters.clear()
        self._parent_map.clear()  # ИСПРАВЛЕНО: Карта родителей очищается при сбросе
        for item in self.scene.items():
            if isinstance(item, (BaseNode, Connection)):
                item.set_active_state(False)

