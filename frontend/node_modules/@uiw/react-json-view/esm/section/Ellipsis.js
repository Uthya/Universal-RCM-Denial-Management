import _extends from "@babel/runtime/helpers/extends";
import _objectWithoutPropertiesLoose from "@babel/runtime/helpers/objectWithoutPropertiesLoose";
var _excluded = ["as", "render"];
import { useSectionStore } from "../store/Section.js";
import { useSectionRender } from "../utils/useRender.js";
import { jsx as _jsx } from "react/jsx-runtime";
export var Ellipsis = props => {
  var _useSectionStore = useSectionStore(),
    _useSectionStore$Elli = _useSectionStore.Ellipsis,
    Comp = _useSectionStore$Elli === void 0 ? {} : _useSectionStore$Elli;
  useSectionRender(Comp, props, 'Ellipsis');
  return null;
};
Ellipsis.displayName = 'JVR.Ellipsis';
export var EllipsisComp = _ref => {
  var isExpanded = _ref.isExpanded,
    value = _ref.value,
    keyName = _ref.keyName;
  var _useSectionStore2 = useSectionStore(),
    _useSectionStore2$Ell = _useSectionStore2.Ellipsis,
    Comp = _useSectionStore2$Ell === void 0 ? {} : _useSectionStore2$Ell;
  var as = Comp.as,
    render = Comp.render,
    reset = _objectWithoutPropertiesLoose(Comp, _excluded);
  var Elm = as || 'span';
  var child = render && typeof render === 'function' && render(_extends({}, reset, {
    'data-expanded': isExpanded
  }), {
    value,
    keyName
  });
  if (child) return child;
  if (!isExpanded || typeof value === 'object' && Object.keys(value).length == 0) return null;
  return /*#__PURE__*/_jsx(Elm, _extends({}, reset));
};
EllipsisComp.displayName = 'JVR.EllipsisComp';