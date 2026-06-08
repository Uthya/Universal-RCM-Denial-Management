import _extends from "@babel/runtime/helpers/extends";
import _objectWithoutPropertiesLoose from "@babel/runtime/helpers/objectWithoutPropertiesLoose";
var _excluded = ["children", "value", "parentValue", "keyName", "keys"],
  _excluded2 = ["as", "render", "children"];
import { useSectionStore } from "../store/Section.js";
import { useSectionRender } from "../utils/useRender.js";
import { jsx as _jsx } from "react/jsx-runtime";
export var Row = props => {
  var _useSectionStore = useSectionStore(),
    _useSectionStore$Row = _useSectionStore.Row,
    Comp = _useSectionStore$Row === void 0 ? {} : _useSectionStore$Row;
  useSectionRender(Comp, props, 'Row');
  return null;
};
Row.displayName = 'JVR.Row';
export var RowComp = props => {
  var children = props.children,
    value = props.value,
    parentValue = props.parentValue,
    keyName = props.keyName,
    keys = props.keys,
    other = _objectWithoutPropertiesLoose(props, _excluded);
  var _useSectionStore2 = useSectionStore(),
    _useSectionStore2$Row = _useSectionStore2.Row,
    Comp = _useSectionStore2$Row === void 0 ? {} : _useSectionStore2$Row;
  var as = Comp.as,
    render = Comp.render,
    _ = Comp.children,
    reset = _objectWithoutPropertiesLoose(Comp, _excluded2);
  var Elm = as || 'div';
  var child = render && typeof render === 'function' && render(_extends({}, other, reset, {
    children
  }), {
    value,
    keyName,
    parentValue,
    keys
  });
  if (child) return child;
  return /*#__PURE__*/_jsx(Elm, _extends({}, other, reset, {
    children: children
  }));
};
RowComp.displayName = 'JVR.RowComp';