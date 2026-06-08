import _extends from "@babel/runtime/helpers/extends";
import _objectWithoutPropertiesLoose from "@babel/runtime/helpers/objectWithoutPropertiesLoose";
var _excluded = ["value", "keyName"],
  _excluded2 = ["as", "render"];
import { useSectionStore } from "../store/Section.js";
import { useSectionRender } from "../utils/useRender.js";
import { jsx as _jsx } from "react/jsx-runtime";
export var CountInfoExtra = props => {
  var _useSectionStore = useSectionStore(),
    _useSectionStore$Coun = _useSectionStore.CountInfoExtra,
    Comp = _useSectionStore$Coun === void 0 ? {} : _useSectionStore$Coun;
  useSectionRender(Comp, props, 'CountInfoExtra');
  return null;
};
CountInfoExtra.displayName = 'JVR.CountInfoExtra';
export var CountInfoExtraComps = props => {
  var _props$value = props.value,
    value = _props$value === void 0 ? {} : _props$value,
    keyName = props.keyName,
    other = _objectWithoutPropertiesLoose(props, _excluded);
  var _useSectionStore2 = useSectionStore(),
    _useSectionStore2$Cou = _useSectionStore2.CountInfoExtra,
    Comp = _useSectionStore2$Cou === void 0 ? {} : _useSectionStore2$Cou;
  var as = Comp.as,
    render = Comp.render,
    reset = _objectWithoutPropertiesLoose(Comp, _excluded2);
  if (!render && !reset.children) return null;
  var Elm = as || 'span';
  var isRender = render && typeof render === 'function';
  var elmProps = _extends({}, reset, other);
  var child = isRender && render(elmProps, {
    value,
    keyName
  });
  if (child) return child;
  return /*#__PURE__*/_jsx(Elm, _extends({}, elmProps));
};
CountInfoExtraComps.displayName = 'JVR.CountInfoExtraComps';