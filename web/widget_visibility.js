// UE draws connectable widget slots even when the owning widget is hidden.
// Disable only those virtual connections; restore the user's choice when shown.
export function syncHiddenWidgetConnections(node) {
  const flags=node.properties?.ue_properties?.widget_ue_connectable;
  if (!flags) return;
  const saved=node.properties.h3lv_hidden_ue_widgets || {};
  for (const widget of node.widgets || []) {
    const input=node.inputs?.find(item=>item.name===widget.name && item.widget);
    if (!input) continue;
    // A real wired parameter must stay usable, even if its widget is hidden.
    const hidden=widget.hidden && input.link==null;
    if (hidden && flags[widget.name]) {
      saved[widget.name]=flags[widget.name];
      flags[widget.name]=false;
    } else if (!hidden && Object.hasOwn(saved,widget.name)) {
      flags[widget.name]=saved[widget.name];
      delete saved[widget.name];
    }
  }
  if (Object.keys(saved).length) node.properties.h3lv_hidden_ue_widgets=saved;
  else delete node.properties.h3lv_hidden_ue_widgets;
}
