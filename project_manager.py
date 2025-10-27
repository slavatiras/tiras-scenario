# -*- coding: utf-8 -*-
import uuid
import logging
from copy import deepcopy
from PyQt6.QtCore import QObject, pyqtSignal # Додаємо QObject та pyqtSignal

log = logging.getLogger(__name__)

# Визначення конфігурацій пристроїв (перенесено сюди з main_window)
DEVICE_SPECS = {
    "MOUT8R": {"type": "Модуль релейних виходів", "outputs": 8, "zones": 0},
    "PUIZ 2": {"type": "Пристрій індикації", "outputs": 0, "zones": 2},
    "ППКП Tiras-8L": {"type": "Базовий прилад", "outputs": 2, "zones": 8}
}

class ProjectManager(QObject): # Наслідуємо QObject для сигналів
    """
    Клас для управління даними проекту: сценаріями, макросами та конфігурацією.
    """
    # Сигнал, що сповіщає про оновлення даних проекту (для оновлення UI)
    project_updated = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.project_data = {}
        log.info("ProjectManager initialized.")
        # self.new_project() # Не викликаємо тут, щоб уникнути подвійної ініціалізації

    def new_project(self):
        """Створює структуру даних для нового порожнього проекту."""
        log.info("Creating new project structure.")
        self.project_data = {
            'scenarios': {},
            'macros': {},
            'config': {
                'devices': [],
                'users': [{'id': str(uuid.uuid4()), 'name': 'Адміністратор', 'phone': '+380000000000'}]
            }
        }
        # Додаємо базовий прилад за замовчуванням
        self.add_device("ППКП Tiras-8L", emit_signal=False) # Не сповіщаємо про оновлення тут
        # Додаємо перший сценарій
        self.add_scenario("Сценарій 1", emit_signal=False) # Не сповіщаємо про оновлення тут
        self.project_updated.emit() # Сповіщаємо один раз в кінці

    def load_project(self, data):
        """Завантажує дані існуючого проекту."""
        log.info("Loading project data into ProjectManager.")
        if isinstance(data, dict):
            # TODO: Додати валідацію структури даних 'data'
            self.project_data = data
            # Переконатись, що основні ключі існують
            self.project_data.setdefault('scenarios', {})
            self.project_data.setdefault('macros', {})
            self.project_data.setdefault('config', {'devices': [], 'users': []})
            self.project_data['config'].setdefault('devices', [])
            self.project_data['config'].setdefault('users', [])
            log.debug(f"Project data loaded. Scenarios: {len(self.project_data['scenarios'])}, Macros: {len(self.project_data['macros'])}")
            self.project_updated.emit() # Сповістити про оновлення
            return True
        else:
            log.error("Failed to load project data: Invalid data format.")
            return False

    def get_project_data(self):
        """Повертає копію поточних даних проекту."""
        return deepcopy(self.project_data)

    # --- Scenario Management ---

    def get_scenario_ids(self):
        """Повертає відсортований список ID (імен) сценаріїв."""
        return sorted(self.project_data.get('scenarios', {}).keys())

    def get_first_scenario_id(self):
        """Повертає ID першого сценарію зі списку або None."""
        ids = self.get_scenario_ids()
        return ids[0] if ids else None

    def get_scenario_data(self, scenario_id):
        """Повертає дані конкретного сценарію або None."""
        return self.project_data.get('scenarios', {}).get(scenario_id)

    def add_scenario(self, name=None, emit_signal=True):
        """
        Додає новий порожній сценарій. Генерує унікальне ім'я, якщо не надано.
        Повертає ім'я створеного сценарію або None у разі помилки.
        """
        scenarios = self.project_data.setdefault('scenarios', {})
        if name is None:
            i = 1
            base_name = "Новий сценарій"
            name = base_name
            while name in scenarios:
                name = f"{base_name} {i}"
                i += 1
        elif name in scenarios:
            log.warning(f"Scenario '{name}' already exists. Cannot add.")
            return None # Ім'я вже зайняте

        log.info(f"Adding new scenario: {name}")
        scenarios[name] = {'nodes': [], 'connections': [], 'comments': [], 'frames': []}
        if emit_signal:
            self.project_updated.emit() # Сповістити про оновлення
        return name

    def remove_scenario(self, scenario_id, emit_signal=True):
        """Видаляє сценарій за ID (іменем). Повертає True при успіху."""
        scenarios = self.project_data.get('scenarios', {})
        if scenario_id in scenarios:
            log.info(f"Removing scenario: {scenario_id}")
            del scenarios[scenario_id]
            if emit_signal:
                self.project_updated.emit()
            return True
        else:
            log.warning(f"Scenario '{scenario_id}' not found. Cannot remove.")
            return False

    def rename_scenario(self, old_id, new_id, emit_signal=True):
        """Перейменовує сценарій. Повертає True при успіху."""
        scenarios = self.project_data.get('scenarios', {})
        if old_id not in scenarios:
            log.warning(f"Scenario '{old_id}' not found. Cannot rename.")
            return False
        if new_id == old_id:
            return True # Ім'я не змінилось
        if new_id in scenarios:
            log.warning(f"Scenario name '{new_id}' already exists. Cannot rename.")
            return False

        log.info(f"Renaming scenario '{old_id}' to '{new_id}'")
        scenarios[new_id] = scenarios.pop(old_id)
        if emit_signal:
            self.project_updated.emit()
        return True

    def update_scenario_data(self, scenario_id, scene_data, emit_signal=False):
        """Оновлює дані сценарію даними зі сцени."""
        if scenario_id in self.project_data.get('scenarios', {}):
            log.debug(f"Updating data for scenario: {scenario_id}")
            # TODO: Додати перевірку валідності scene_data?
            self.project_data['scenarios'][scenario_id] = scene_data
            if emit_signal: # Зазвичай не потрібно сповіщати UI при кожному збереженні стану
                self.project_updated.emit()
            return True
        else:
            log.warning(f"Scenario '{scenario_id}' not found. Cannot update data.")
            return False

    # --- Macro Management ---

    def get_macros_data(self):
        """Повертає словник з усіма визначеннями макросів."""
        return self.project_data.get('macros', {})

    def get_macro_data(self, macro_id):
        """Повертає дані конкретного макросу або None."""
        return self.project_data.get('macros', {}).get(macro_id)

    def add_or_update_macro(self, macro_id, macro_data, emit_signal=True):
        """Додає новий макрос або оновлює існуючий."""
        if not macro_id or not isinstance(macro_data, dict):
             log.error("Cannot add/update macro: Invalid ID or data.")
             return False
        log.info(f"Adding/Updating macro definition: {macro_id} (Name: {macro_data.get('name', '?')})")
        macros = self.project_data.setdefault('macros', {})
        macros[macro_id] = macro_data
        if emit_signal:
             self.project_updated.emit()
        return True

    def remove_macro(self, macro_id, emit_signal=True):
        """Видаляє визначення макросу. Повертає True при успіху."""
        macros = self.project_data.get('macros', {})
        if macro_id in macros:
            log.info(f"Removing macro definition: {macro_id}")
            del macros[macro_id]
            if emit_signal:
                self.project_updated.emit()
            return True
        else:
            log.warning(f"Macro definition '{macro_id}' not found. Cannot remove.")
            return False

    def rename_macro(self, macro_id, new_name, emit_signal=True):
        """Перейменовує макрос. Повертає True при успіху."""
        macros = self.project_data.get('macros', {})
        if macro_id not in macros:
            log.warning(f"Macro definition '{macro_id}' not found. Cannot rename.")
            return False
        if macros[macro_id].get('name') == new_name:
            return True # Ім'я не змінилось

        # Перевірка на унікальність нового імені
        if self.is_macro_name_taken(new_name, exclude_id=macro_id):
             log.warning(f"Macro name '{new_name}' already exists. Cannot rename.")
             return False

        log.info(f"Renaming macro '{macro_id}' to '{new_name}'")
        macros[macro_id]['name'] = new_name
        if emit_signal:
            self.project_updated.emit()
        return True

    # --- ДОДАНО МЕТОД ---
    def is_macro_name_taken(self, name, exclude_id=None):
        """Перевіряє, чи існує макрос з таким ім'ям (окрім зазначеного ID)."""
        if not name: return False # Порожнє ім'я вважається не зайнятим
        macros = self.project_data.get('macros', {})
        for mid, mdata in macros.items():
            if mid == exclude_id:
                continue # Пропускаємо макрос, який перейменовуємо
            if mdata.get('name') == name:
                log.debug(f"Macro name '{name}' is already taken by macro {mid}.") # Діагностичне повідомлення
                return True
        log.debug(f"Macro name '{name}' is available.") # Діагностичне повідомлення
        return False
    # --- КІНЕЦЬ ДОДАНОГО МЕТОДУ ---

    def update_macro_data(self, macro_id, scene_data, emit_signal=False):
        """
        Оновлює вузли та з'єднання макросу даними зі сцени,
        а також оновлює списки 'inputs'/'outputs' у визначенні макросу.
        Повертає оновлені дані макросу, якщо списки IO змінилися, інакше None.
        """
        macros = self.project_data.get('macros', {})
        if macro_id in macros:
            log.debug(f"Updating data for macro: {macro_id}")
            macro_data = macros[macro_id]
            old_inputs = deepcopy(macro_data.get('inputs', [])) # Зберігаємо старі
            old_outputs = deepcopy(macro_data.get('outputs', []))

            # Оновлюємо базові дані зі сцени
            macro_data['nodes'] = scene_data.get('nodes', [])
            macro_data['connections'] = scene_data.get('connections', [])
            macro_data['comments'] = scene_data.get('comments', []) # Додаємо збереження
            macro_data['frames'] = scene_data.get('frames', [])   # Додаємо збереження

            # Оновлюємо списки inputs/outputs на основі вузлів на сцені
            new_inputs = []
            new_outputs = []
            for node_data in macro_data['nodes']:
                node_type = node_data.get('node_type')
                node_id = node_data.get('id')
                node_name = node_data.get('name')
                if node_type == 'MacroInputNode':
                    new_inputs.append({'name': node_name, 'macro_input_node_id': node_id})
                elif node_type == 'MacroOutputNode':
                    new_outputs.append({'name': node_name, 'macro_output_node_id': node_id})

            # Сортуємо для консистентності (не обов'язково, але корисно)
            new_inputs.sort(key=lambda x: x.get('name', ''))
            new_outputs.sort(key=lambda x: x.get('name', ''))

            # Перевіряємо, чи змінились входи/виходи
            io_changed = (old_inputs != new_inputs or old_outputs != new_outputs)
            log.debug(f"  Macro IO changed: {io_changed}")

            macro_data['inputs'] = new_inputs
            macro_data['outputs'] = new_outputs

            if emit_signal:
                self.project_updated.emit()

            # Повертаємо оновлені дані, якщо IO змінилися, щоб MainWindow міг оновити MacroNode
            return macro_data if io_changed else None
        else:
            log.warning(f"Macro definition '{macro_id}' not found. Cannot update data.")
            return None

    def update_macro_io_name(self, macro_id, io_node_id, io_type, new_name, emit_signal=True):
        """Оновлює ім'я входу або виходу у визначенні макросу."""
        macro_data = self.get_macro_data(macro_id)
        if not macro_data:
            log.warning(f"Cannot update IO name: Macro {macro_id} not found.")
            return False

        io_list_key = 'inputs' if io_type == 'input' else 'outputs'
        node_id_key = 'macro_input_node_id' if io_type == 'input' else 'macro_output_node_id'

        io_list = macro_data.setdefault(io_list_key, [])
        found = False
        io_changed = False
        for io_def in io_list:
            if io_def.get(node_id_key) == io_node_id:
                if io_def.get('name') != new_name:
                    log.debug(f"Updating macro {macro_id} {io_type} node {io_node_id} name to '{new_name}'")
                    io_def['name'] = new_name
                    io_changed = True
                found = True
                break

        if not found:
             log.warning(f"Could not find {io_type} definition for node {io_node_id} in macro {macro_id}")
             return False

        if io_changed and emit_signal:
             self.project_updated.emit() # Можна сповіщати, якщо це важливо для UI

        return io_changed # Повертаємо True, якщо ім'я дійсно змінилось

    def check_macro_usage(self, macro_id):
        """Перевіряє, чи використовується макрос у сценаріях."""
        usage_count = 0
        usage_scenarios = []
        for scenario_id, scenario_data in self.project_data.get('scenarios', {}).items():
            for node_data in scenario_data.get('nodes', []):
                # Перевіряємо за ім'ям класу та ID макросу
                if node_data.get('node_type') == 'MacroNode' and node_data.get('macro_id') == macro_id:
                    usage_count += 1
                    if scenario_id not in usage_scenarios:
                        usage_scenarios.append(scenario_id)
        log.debug(f"Macro {macro_id} usage check: Count={usage_count}, Scenarios={usage_scenarios}")
        return usage_count, usage_scenarios

    # --- Configuration Management ---

    def get_config_data(self):
        """Повертає словник з конфігурацією системи."""
        return self.project_data.get('config', {})

    def get_all_zones_and_outputs(self):
        """Повертає списки всіх зон та виходів з усіх пристроїв."""
        all_zones, all_outputs = [], []
        config = self.get_config_data()
        for device in config.get('devices', []):
            # Додаємо інформацію про батьківський пристрій до кожної зони/виходу
            parent_name = device.get('name', '')
            for zone in device.get('zones', []):
                zone_copy = zone.copy()
                zone_copy['parent_name'] = parent_name
                all_zones.append(zone_copy)
            for output in device.get('outputs', []):
                output_copy = output.copy()
                output_copy['parent_name'] = parent_name
                all_outputs.append(output_copy)
        return all_zones, all_outputs

    def add_device(self, device_type, emit_signal=True):
        """Додає новий пристрій до конфігурації."""
        if device_type not in DEVICE_SPECS:
            log.warning(f"Cannot add device: Unknown type '{device_type}'")
            return None
        spec = DEVICE_SPECS[device_type]
        config = self.project_data.setdefault('config', {})
        devices = config.setdefault('devices', [])

        device_count = sum(1 for d in devices if d.get('type') == device_type)
        new_device_id = str(uuid.uuid4())
        new_device_name = f"{device_type} #{device_count + 1}"
        new_device = {'id': new_device_id, 'name': new_device_name, 'type': device_type,
                      'zones': [], 'outputs': []}

        for i in range(spec['zones']):
            new_device['zones'].append({'id': str(uuid.uuid4()), 'name': f"Зона {i + 1}"})
        for i in range(spec['outputs']):
            new_device['outputs'].append({'id': str(uuid.uuid4()), 'name': f"Вихід {i + 1}"})

        log.info(f"Adding device: {new_device_name} (ID: {new_device_id})")
        devices.append(new_device)
        if emit_signal:
            self.project_updated.emit()
        return new_device_id

    def remove_device(self, device_id, emit_signal=True):
        """Видаляє пристрій з конфігурації. Повертає True при успіху."""
        config = self.project_data.get('config', {})
        devices = config.get('devices', [])
        initial_len = len(devices)
        devices[:] = [d for d in devices if d.get('id') != device_id] # Видалення зі списку
        removed = len(devices) < initial_len

        if removed:
            log.info(f"Removed device: {device_id}")
            if emit_signal:
                self.project_updated.emit()
            return True
        else:
            log.warning(f"Device '{device_id}' not found. Cannot remove.")
            return False

    def add_user(self, name=None, phone='', emit_signal=True):
        """Додає нового користувача."""
        config = self.project_data.setdefault('config', {})
        users = config.setdefault('users', [])
        if name is None:
             name = f"Новий користувач {len(users) + 1}"
        new_user_id = str(uuid.uuid4())
        new_user = {'id': new_user_id, 'name': name, 'phone': phone}
        log.info(f"Adding user: {name} (ID: {new_user_id})")
        users.append(new_user)
        if emit_signal:
            self.project_updated.emit()
        return new_user_id

    def remove_user(self, user_id, emit_signal=True):
        """Видаляє користувача. Повертає True при успіху."""
        config = self.project_data.get('config', {})
        users = config.get('users', [])
        initial_len = len(users)
        users[:] = [u for u in users if u.get('id') != user_id]
        removed = len(users) < initial_len
        if removed:
            log.info(f"Removed user: {user_id}")
            if emit_signal:
                self.project_updated.emit()
            return True
        else:
            log.warning(f"User '{user_id}' not found. Cannot remove.")
            return False

    def update_config_item(self, item_type, item_id, data_key, new_value, emit_signal=False):
        """
        Оновлює поле (data_key) для елемента конфігурації (item_type) з вказаним ID.
        item_type може бути 'devices', 'zones', 'outputs', 'users'.
        Повертає True, якщо оновлення відбулося.
        """
        log.debug(f"Updating config: Type={item_type}, ID={item_id}, Key={data_key}, Value={new_value}")
        config = self.project_data.get('config', {})
        updated = False

        if item_type == 'devices':
            for device in config.get('devices', []):
                if device.get('id') == item_id:
                    if device.get(data_key) != new_value:
                        device[data_key] = new_value
                        log.info(f"Device {item_id} updated: {data_key} = {new_value}")
                        updated = True
                        # Якщо змінили ім'я пристрою, оновити parent_name у його зонах/виходах не потрібно,
                        # оскільки get_all_zones_and_outputs робить це динамічно.
                    break
        elif item_type == 'zones':
            for device in config.get('devices', []):
                for zone in device.get('zones', []):
                    if zone.get('id') == item_id:
                        if zone.get(data_key) != new_value:
                            zone[data_key] = new_value
                            log.info(f"Zone {item_id} (in {device.get('id')}) updated: {data_key} = {new_value}")
                            updated = True
                        break # Знайшли зону, виходимо з внутрішнього циклу
                if updated: break # Виходимо із зовнішнього циклу, якщо вже оновили
        elif item_type == 'outputs':
            for device in config.get('devices', []):
                for output in device.get('outputs', []):
                     if output.get('id') == item_id:
                         if output.get(data_key) != new_value:
                              output[data_key] = new_value
                              log.info(f"Output {item_id} (in {device.get('id')}) updated: {data_key} = {new_value}")
                              updated = True
                         break
                if updated: break
        elif item_type == 'users':
            for user in config.get('users', []):
                if user.get('id') == item_id:
                    if user.get(data_key) != new_value:
                        user[data_key] = new_value
                        log.info(f"User {item_id} updated: {data_key} = {new_value}")
                        updated = True
                    break

        if updated and emit_signal: # Зазвичай оновлення відбувається з UI, тому сигнал не потрібен тут
            self.project_updated.emit()

        return updated

