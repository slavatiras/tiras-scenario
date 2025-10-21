import sys
import logging
from PyQt6.QtWidgets import QApplication
from main_window import MainWindow

# ДОБАВЛЕНО: Настройка системы логирования
# Все сообщения уровня DEBUG и выше будут записываться в файл editor.log
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    filename='editor.log',
    filemode='w' # 'w' - перезаписывать файл при каждом запуске, 'a' - дописывать
)

# Создаем логгер для вывода в консоль
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
formatter = logging.Formatter('%(name)-12s: %(levelname)-8s %(message)s')
console_handler.setFormatter(formatter)
logging.getLogger('').addHandler(console_handler)

log = logging.getLogger(__name__)


if __name__ == "__main__":
    log.info("Запуск приложения...")
    try:
        app = QApplication(sys.argv)
        window = MainWindow()
        window.show()
        sys.exit(app.exec())
    except Exception as e:
        log.critical(f"Критическая ошибка в приложении: {e}", exc_info=True)
        sys.exit(1)