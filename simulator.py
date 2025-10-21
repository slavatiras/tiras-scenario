from nodes import TriggerNode, BaseNode, Connection


class ScenarioSimulator:
    def __init__(self, scene, main_window):
        self.scene = scene
        self.main_window = main_window
        self.is_running = False
        self.current_nodes = []
        self.history = []

    def start(self, trigger_zone_id):
        if self.is_running:
            return

        self.reset()
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

        next_nodes = []
        nodes_to_deactivate = []

        for node in self.current_nodes:
            nodes_to_deactivate.append(node)
            if node.in_socket:
                for conn in node.in_socket.connections:
                    if conn.start_socket.parentItem() in self.history:
                        conn.set_active_state(False)

            if node.out_socket:
                for conn in node.out_socket.connections:
                    child_node = conn.end_socket.parentItem()
                    if isinstance(child_node, BaseNode):
                        conn.set_active_state(True)
                        child_node.set_active_state(True)
                        next_nodes.append(child_node)
                        if child_node not in self.history:
                            self.history.append(child_node)

        for node in nodes_to_deactivate:
            node.set_active_state(False)

        self.current_nodes = next_nodes

        if not self.current_nodes:
            self.is_running = False

    def stop(self):
        if not self.is_running and not self.current_nodes:
            return
        self.reset()
        self.main_window.show_status_message("Симуляцію зупинено.")

    def reset(self):
        self.is_running = False
        self.current_nodes = []
        self.history = []
        for item in self.scene.items():
            if isinstance(item, (BaseNode, Connection)):
                item.set_active_state(False)

