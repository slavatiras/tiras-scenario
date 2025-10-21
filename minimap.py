from PyQt6.QtWidgets import QGraphicsView
from PyQt6.QtGui import QPainter, QPen, QColor, QBrush
from PyQt6.QtCore import Qt


# Этот новый файл определяет виджет Мини-карты.
# Он отображает уменьшенную версию всей сцены и прямоугольник,
# показывающий текущую видимую область основного редактора.

class Minimap(QGraphicsView):
    """
    Виджет мини-карты, который показывает обзор всей сцены и позволяет быстро навигировать.
    """

    def __init__(self, main_view):
        super().__init__(main_view)
        self.main_view = main_view
        # Мини-карта использует ту же сцену, что и основной редактор
        self.setScene(self.main_view.scene())
        self.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Стилизация
        self.setStyleSheet("border: 1px solid #555; background-color: #2a2a2a;")

        # Настройки для прямоугольника, показывающего видимую область
        self.viewport_rect_pen = QPen(QColor("#77aaff"), 1.5)
        self.viewport_rect_brush = QBrush(QColor(100, 100, 200, 70))

        # По умолчанию мини-карта не интерактивна, чтобы не мешать основному виду,
        # но мы перехватываем события мыши напрямую.
        self.setInteractive(False)

    def update_view(self):
        """Обновляет вид мини-карты, чтобы вместить всю сцену, и перерисовывает ее."""
        if self.scene() and self.scene().sceneRect().width() > 0:
            # Вписываем всю сцену в виджет мини-карты с сохранением пропорций
            self.fitInView(self.scene().sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self.update()

    def drawForeground(self, painter, rect):
        """Отрисовывает прямоугольник видимой области поверх вида мини-карты."""
        super().drawForeground(painter, rect)

        # Получаем видимый прямоугольник из основного вида в координатах сцены
        main_view_rect = self.main_view.mapToScene(self.main_view.viewport().rect()).boundingRect()

        painter.setPen(self.viewport_rect_pen)
        painter.setBrush(self.viewport_rect_brush)

        # Прямоугольник уже в координатах сцены, поэтому его можно сразу рисовать
        painter.drawRect(main_view_rect)

    def mousePressEvent(self, event):
        """При клике на мини-карту центрирует основной вид на этой точке."""
        self.center_main_view(event.pos())

    def mouseMoveEvent(self, event):
        """При перетаскивании мыши по мини-карте продолжает центрировать основной вид."""
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.center_main_view(event.pos())

    def center_main_view(self, pos):
        """Центрирует основной редактор на точке, соответствующей клику на мини-карте."""
        scene_pos = self.mapToScene(pos)
        self.main_view.centerOn(scene_pos)