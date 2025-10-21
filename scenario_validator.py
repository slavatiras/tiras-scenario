from nodes import TriggerNode, ActivateOutputNode, DeactivateOutputNode, SendSMSNode


def build_graph(nodes, connections):
    """Строит ориентированный граф из узлов и связей."""
    adj = {node.id: [] for node in nodes}
    for conn in connections:
        start_node = conn.start_socket.parentItem()
        end_node = conn.end_socket.parentItem()
        if start_node and end_node:
            adj[start_node.id].append(end_node.id)
    return adj


def find_unreachable_nodes(nodes, connections):
    """
    Находит все узлы, до которых невозможно добраться, начиная от триггера.
    Использует обход в ширину (BFS).
    """
    trigger = next((n for n in nodes if isinstance(n, TriggerNode)), None)
    if not trigger:
        # Если триггера нет, все узлы считаются недостижимыми.
        return [n.id for n in nodes]

    graph = build_graph(nodes, connections)
    queue = [trigger.id]
    visited = {trigger.id}

    while queue:
        u_id = queue.pop(0)
        for v_id in graph.get(u_id, []):
            if v_id not in visited:
                visited.add(v_id)
                queue.append(v_id)

    all_node_ids = {node.id for node in nodes}
    unreachable_ids = list(all_node_ids - visited)
    return unreachable_ids


def find_dangling_outputs(nodes, connections):
    """
    Находит узлы, которые не являются конечными действиями,
    но из которых не исходят связи.
    """
    dangling_ids = []
    # Узлы, которые по своей природе являются конечными точками сценария
    terminal_node_types = (ActivateOutputNode, DeactivateOutputNode, SendSMSNode)
    nodes_with_outgoing_connections = set()

    for conn in connections:
        start_node = conn.start_socket.parentItem()
        if start_node:
            nodes_with_outgoing_connections.add(start_node.id)

    for node in nodes:
        # Узел является "висячим", если он:
        # 1. Не является терминальным по своей природе.
        # 2. Имеет выходной сокет.
        # 3. Не имеет исходящих соединений.
        if (not isinstance(node, terminal_node_types) and
                node.out_socket and
                node.id not in nodes_with_outgoing_connections):
            dangling_ids.append(node.id)

    return dangling_ids

