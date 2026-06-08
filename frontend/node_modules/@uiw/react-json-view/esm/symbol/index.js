import _objectDestructuringEmpty from "@babel/runtime/helpers/objectDestructuringEmpty";
import _extends from "@babel/runtime/helpers/extends";
import _objectWithoutPropertiesLoose from "@babel/runtime/helpers/objectWithoutPropertiesLoose";
var _excluded = ["isNumber", "value", "parentValue", "keyName", "keys"],
  _excluded2 = ["as", "render"],
  _excluded3 = ["as", "render"],
  _excluded4 = ["as", "render"],
  _excluded5 = ["as", "style", "render"],
  _excluded6 = ["as", "render"],
  _excluded7 = ["as", "render"],
  _excluded8 = ["as", "render"],
  _excluded9 = ["as", "render"];
import { useSymbolsStore } from "../store/Symbols.js";
import { useExpandsStore } from "../store/Expands.js";
import { jsx as _jsx } from "react/jsx-runtime";
export var Quote = props => {
  var _useSymbolsStore = useSymbolsStore(),
    _useSymbolsStore$Quot = _useSymbolsStore.Quote,
    Comp = _useSymbolsStore$Quot === void 0 ? {} : _useSymbolsStore$Quot;
  var isNumber = props.isNumber,
    value = props.value,
    parentValue = props.parentValue,
    keyName = props.keyName,
    keys = props.keys,
    other = _objectWithoutPropertiesLoose(props, _excluded);
  if (isNumber) return null;
  var as = Comp.as,
    render = Comp.render,
    reset = _objectWithoutPropertiesLoose(Comp, _excluded2);
  var Elm = as || 'span';
  var elmProps = _extends({}, other, reset);
  // Trim the leading and trailing spaces in children,
  // and set it to undefined if it's an empty string or contains only spaces.
  // https://github.com/uiwjs/react-json-view/issues/86
  if (typeof elmProps.children === 'string') {
    var trimmed = elmProps.children.trim();
    elmProps.children = trimmed || undefined;
  }
  var result = {
    value,
    parentValue,
    keyName,
    keys: keys || (keyName ? [keyName] : [])
  };
  var child = render && typeof render === 'function' && render(elmProps, result);
  if (child) return child;
  return /*#__PURE__*/_jsx(Elm, _extends({}, elmProps));
};
Quote.displayName = 'JVR.Quote';
export var ValueQuote = props => {
  var _useSymbolsStore2 = useSymbolsStore(),
    _useSymbolsStore2$Val = _useSymbolsStore2.ValueQuote,
    Comp = _useSymbolsStore2$Val === void 0 ? {} : _useSymbolsStore2$Val;
  var other = _extends({}, (_objectDestructuringEmpty(props), props));
  var as = Comp.as,
    render = Comp.render,
    reset = _objectWithoutPropertiesLoose(Comp, _excluded3);
  var Elm = as || 'span';
  var elmProps = _extends({}, other, reset);
  var child = render && typeof render === 'function' && render(elmProps, {});
  if (child) return child;
  return /*#__PURE__*/_jsx(Elm, _extends({}, elmProps));
};
ValueQuote.displayName = 'JVR.ValueQuote';
export var Colon = props => {
  var value = props.value,
    parentValue = props.parentValue,
    keyName = props.keyName,
    keys = props.keys;
  var _useSymbolsStore3 = useSymbolsStore(),
    _useSymbolsStore3$Col = _useSymbolsStore3.Colon,
    Comp = _useSymbolsStore3$Col === void 0 ? {} : _useSymbolsStore3$Col;
  var as = Comp.as,
    render = Comp.render,
    reset = _objectWithoutPropertiesLoose(Comp, _excluded4);
  var Elm = as || 'span';
  var child = render && typeof render === 'function' && render(reset, {
    value,
    parentValue,
    keyName,
    keys: keys || (keyName ? [keyName] : [])
  });
  if (child) return child;
  return /*#__PURE__*/_jsx(Elm, _extends({}, reset));
};
Colon.displayName = 'JVR.Colon';
export var Arrow = props => {
  var _useSymbolsStore4 = useSymbolsStore(),
    _useSymbolsStore4$Arr = _useSymbolsStore4.Arrow,
    Comp = _useSymbolsStore4$Arr === void 0 ? {} : _useSymbolsStore4$Arr;
  var expands = useExpandsStore();
  var expandKey = props.expandKey,
    resetStyle = props.style,
    value = props.value,
    parentValue = props.parentValue,
    keyName = props.keyName,
    keys = props.keys;
  var isExpanded = !!expands[expandKey];
  var as = Comp.as,
    style = Comp.style,
    render = Comp.render,
    reset = _objectWithoutPropertiesLoose(Comp, _excluded5);
  var Elm = as || 'span';
  var isRender = render && typeof render === 'function';
  var elmProps = _extends({}, reset, {
    'data-expanded': isExpanded,
    style: _extends({}, style, resetStyle)
  });
  var result = {
    value,
    parentValue,
    keyName,
    keys: keys || (keyName ? [keyName] : [])
  };
  var child = isRender && render(elmProps, result);
  if (child) return child;
  return /*#__PURE__*/_jsx(Elm, _extends({}, reset, {
    style: _extends({}, style, resetStyle)
  }));
};
Arrow.displayName = 'JVR.Arrow';
export var BracketsOpen = props => {
  var isBrackets = props.isBrackets,
    value = props.value,
    parentValue = props.parentValue,
    keyName = props.keyName,
    keys = props.keys;
  var _useSymbolsStore5 = useSymbolsStore(),
    _useSymbolsStore5$Bra = _useSymbolsStore5.BracketsLeft,
    BracketsLeft = _useSymbolsStore5$Bra === void 0 ? {} : _useSymbolsStore5$Bra,
    _useSymbolsStore5$Bra2 = _useSymbolsStore5.BraceLeft,
    BraceLeft = _useSymbolsStore5$Bra2 === void 0 ? {} : _useSymbolsStore5$Bra2;
  var result = {
    value,
    parentValue,
    keyName,
    keys: keys || (keyName ? [keyName] : [])
  };
  if (isBrackets) {
    var as = BracketsLeft.as,
      _render = BracketsLeft.render,
      reset = _objectWithoutPropertiesLoose(BracketsLeft, _excluded6);
    var BracketsLeftComp = as || 'span';
    var _child = _render && typeof _render === 'function' && _render(reset, result);
    if (_child) return _child;
    return /*#__PURE__*/_jsx(BracketsLeftComp, _extends({}, reset));
  }
  var elm = BraceLeft.as,
    render = BraceLeft.render,
    resetProps = _objectWithoutPropertiesLoose(BraceLeft, _excluded7);
  var BraceLeftComp = elm || 'span';
  var child = render && typeof render === 'function' && render(resetProps, result);
  if (child) return child;
  return /*#__PURE__*/_jsx(BraceLeftComp, _extends({}, resetProps));
};
BracketsOpen.displayName = 'JVR.BracketsOpen';
export var BracketsClose = props => {
  var isBrackets = props.isBrackets,
    isVisiable = props.isVisiable,
    value = props.value,
    parentValue = props.parentValue,
    keyName = props.keyName,
    keys = props.keys;
  var result = {
    value,
    parentValue,
    keyName,
    keys: keys || (keyName ? [keyName] : [])
  };
  if (!isVisiable) return null;
  var _useSymbolsStore6 = useSymbolsStore(),
    _useSymbolsStore6$Bra = _useSymbolsStore6.BracketsRight,
    BracketsRight = _useSymbolsStore6$Bra === void 0 ? {} : _useSymbolsStore6$Bra,
    _useSymbolsStore6$Bra2 = _useSymbolsStore6.BraceRight,
    BraceRight = _useSymbolsStore6$Bra2 === void 0 ? {} : _useSymbolsStore6$Bra2;
  if (isBrackets) {
    var as = BracketsRight.as,
      _render2 = BracketsRight.render,
      _reset = _objectWithoutPropertiesLoose(BracketsRight, _excluded8);
    var BracketsRightComp = as || 'span';
    var _child2 = _render2 && typeof _render2 === 'function' && _render2(_reset, result);
    if (_child2) return _child2;
    return /*#__PURE__*/_jsx(BracketsRightComp, _extends({}, _reset));
  }
  var elm = BraceRight.as,
    render = BraceRight.render,
    reset = _objectWithoutPropertiesLoose(BraceRight, _excluded9);
  var BraceRightComp = elm || 'span';
  var child = render && typeof render === 'function' && render(reset, result);
  if (child) return child;
  return /*#__PURE__*/_jsx(BraceRightComp, _extends({}, reset));
};
BracketsClose.displayName = 'JVR.BracketsClose';