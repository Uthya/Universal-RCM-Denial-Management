import _extends from "@babel/runtime/helpers/extends";
import _objectWithoutPropertiesLoose from "@babel/runtime/helpers/objectWithoutPropertiesLoose";
var _excluded = ["value", "keyName"],
  _excluded2 = ["as", "render"];
import { useSectionStore } from "../store/Section.js";
import { useSectionRender } from "../utils/useRender.js";
import { useStore } from "../store.js";
import { jsx as _jsx } from "react/jsx-runtime";
export var CountInfo = props => {
  var _useSectionStore = useSectionStore(),
    _useSectionStore$Coun = _useSectionStore.CountInfo,
    Comp = _useSectionStore$Coun === void 0 ? {} : _useSectionStore$Coun;
  useSectionRender(Comp, props, 'CountInfo');
  return null;
};
CountInfo.displayName = 'JVR.CountInfo';
export var CountInfoComp = props => {
  var _props$value = props.value,
    value = _props$value === void 0 ? {} : _props$value,
    keyName = props.keyName,
    other = _objectWithoutPropertiesLoose(props, _excluded);
  var _useStore = useStore(),
    displayObjectSize = _useStore.displayObjectSize;
  var _useSectionStore2 = useSectionStore(),
    _useSectionStore2$Cou = _useSectionStore2.CountInfo,
    Comp = _useSectionStore2$Cou === void 0 ? {} : _useSectionStore2$Cou;
  if (!displayObjectSize) return null;
  var as = Comp.as,
    render = Comp.render,
    reset = _objectWithoutPropertiesLoose(Comp, _excluded2);
  var Elm = as || 'span';
  reset.style = _extends({}, reset.style, props.style);
  var len = Object.keys(value).length;
  if (!reset.children) {
    reset.children = len + " item" + (len === 1 ? '' : 's');
  }
  var elmProps = _extends({}, reset, other);
  var isRender = render && typeof render === 'function';
  var child = isRender && render(_extends({}, elmProps, {
    'data-length': len
  }), {
    value,
    keyName
  });
  if (child) return child;
  return /*#__PURE__*/_jsx(Elm, _extends({}, elmProps));
};
CountInfoComp.displayName = 'JVR.CountInfoComp';