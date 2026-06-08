import _extends from "@babel/runtime/helpers/extends";
import _objectWithoutPropertiesLoose from "@babel/runtime/helpers/objectWithoutPropertiesLoose";
var _excluded = ["as", "render"];
import { useSectionStore } from "../store/Section.js";
import { useSectionRender } from "../utils/useRender.js";
import { jsx as _jsx } from "react/jsx-runtime";
export var KeyName = props => {
  var _useSectionStore = useSectionStore(),
    _useSectionStore$KeyN = _useSectionStore.KeyName,
    Comp = _useSectionStore$KeyN === void 0 ? {} : _useSectionStore$KeyN;
  useSectionRender(Comp, props, 'KeyName');
  return null;
};
KeyName.displayName = 'JVR.KeyName';
export var KeyNameComp = props => {
  var children = props.children,
    value = props.value,
    parentValue = props.parentValue,
    keyName = props.keyName,
    keys = props.keys;
  var isNumber = typeof children === 'number';
  var style = {
    color: isNumber ? 'var(--w-rjv-key-number, #268bd2)' : 'var(--w-rjv-key-string, #002b36)'
  };
  var _useSectionStore2 = useSectionStore(),
    _useSectionStore2$Key = _useSectionStore2.KeyName,
    Comp = _useSectionStore2$Key === void 0 ? {} : _useSectionStore2$Key;
  var as = Comp.as,
    render = Comp.render,
    reset = _objectWithoutPropertiesLoose(Comp, _excluded);
  reset.style = _extends({}, reset.style, style);
  var Elm = as || 'span';
  var child = render && typeof render === 'function' && render(_extends({}, reset, {
    children
  }), {
    value,
    parentValue,
    keyName,
    keys: keys || (keyName ? [keyName] : [])
  });
  if (child) return child;
  return /*#__PURE__*/_jsx(Elm, _extends({}, reset, {
    children: children
  }));
};
KeyNameComp.displayName = 'JVR.KeyNameComp';