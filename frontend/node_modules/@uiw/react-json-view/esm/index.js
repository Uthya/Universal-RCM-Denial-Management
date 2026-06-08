import _extends from "@babel/runtime/helpers/extends";
import _objectWithoutPropertiesLoose from "@babel/runtime/helpers/objectWithoutPropertiesLoose";
var _excluded = ["className", "style", "value", "children", "collapsed", "shouldExpandNodeInitially", "indentWidth", "displayObjectSize", "shortenTextAfterLength", "stringEllipsis", "highlightUpdates", "enableClipboard", "displayDataTypes", "objectSortKeys", "onExpand", "onCopied", "beforeCopy"];
import { forwardRef } from 'react';
import { Provider } from "./store.js";
import { Container } from "./Container.js";
import { BraceLeft } from "./symbol/BraceLeft.js";
import { BraceRight } from "./symbol/BraceRight.js";
import { BracketsLeft } from "./symbol/BracketsLeft.js";
import { BracketsRight } from "./symbol/BracketsRight.js";
import { Arrow } from "./symbol/Arrow.js";
import { Colon } from "./symbol/Colon.js";
import { Quote } from "./symbol/Quote.js";
import { ValueQuote } from "./symbol/ValueQuote.js";
import { Bigint } from "./types/Bigint.js";
import { Date } from "./types/Date.js";
import { False } from "./types/False.js";
import { Float } from "./types/Float.js";
import { Int } from "./types/Int.js";
import { Map } from "./types/Map.js";
import { Nan } from "./types/Nan.js";
import { Null } from "./types/Null.js";
import { Set } from "./types/Set.js";
import { StringText } from "./types/String.js";
import { True } from "./types/True.js";
import { Undefined } from "./types/Undefined.js";
import { Url } from "./types/Url.js";
import { Copied } from "./section/Copied.js";
import { CountInfo } from "./section/CountInfo.js";
import { CountInfoExtra } from "./section/CountInfoExtra.js";
import { Ellipsis } from "./section/Ellipsis.js";
import { KeyName } from "./section/KeyName.js";
import { Row } from "./section/Row.js";
import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
export * from "./store.js";
export * from "./store/Expands.js";
export * from "./store/ShowTools.js";
export * from "./store/Symbols.js";
export * from "./store/Types.js";
export * from "./symbol/index.js";
var JsonView = /*#__PURE__*/forwardRef((props, ref) => {
  var _props$className = props.className,
    className = _props$className === void 0 ? '' : _props$className,
    style = props.style,
    value = props.value,
    children = props.children,
    _props$collapsed = props.collapsed,
    collapsed = _props$collapsed === void 0 ? false : _props$collapsed,
    shouldExpandNodeInitially = props.shouldExpandNodeInitially,
    _props$indentWidth = props.indentWidth,
    indentWidth = _props$indentWidth === void 0 ? 15 : _props$indentWidth,
    _props$displayObjectS = props.displayObjectSize,
    displayObjectSize = _props$displayObjectS === void 0 ? true : _props$displayObjectS,
    _props$shortenTextAft = props.shortenTextAfterLength,
    shortenTextAfterLength = _props$shortenTextAft === void 0 ? 30 : _props$shortenTextAft,
    stringEllipsis = props.stringEllipsis,
    _props$highlightUpdat = props.highlightUpdates,
    highlightUpdates = _props$highlightUpdat === void 0 ? true : _props$highlightUpdat,
    _props$enableClipboar = props.enableClipboard,
    enableClipboard = _props$enableClipboar === void 0 ? true : _props$enableClipboar,
    _props$displayDataTyp = props.displayDataTypes,
    displayDataTypes = _props$displayDataTyp === void 0 ? true : _props$displayDataTyp,
    _props$objectSortKeys = props.objectSortKeys,
    objectSortKeys = _props$objectSortKeys === void 0 ? false : _props$objectSortKeys,
    onExpand = props.onExpand,
    onCopied = props.onCopied,
    beforeCopy = props.beforeCopy,
    elmProps = _objectWithoutPropertiesLoose(props, _excluded);
  var defaultStyle = _extends({
    lineHeight: 1.4,
    fontFamily: 'var(--w-rjv-font-family, Menlo, monospace)',
    color: 'var(--w-rjv-color, #002b36)',
    backgroundColor: 'var(--w-rjv-background-color, #00000000)',
    fontSize: 13
  }, style);
  var cls = ['w-json-view-container', 'w-rjv', className].filter(Boolean).join(' ');
  return /*#__PURE__*/_jsxs(Provider, {
    initialState: {
      value,
      objectSortKeys,
      indentWidth,
      shouldExpandNodeInitially: collapsed === false ? shouldExpandNodeInitially : undefined,
      displayObjectSize,
      collapsed,
      enableClipboard,
      shortenTextAfterLength,
      stringEllipsis,
      highlightUpdates,
      onCopied,
      onExpand,
      beforeCopy
    },
    initialTypes: {
      displayDataTypes
    },
    children: [/*#__PURE__*/_jsx(Container, _extends({
      value: value
    }, elmProps, {
      ref: ref,
      className: cls,
      style: defaultStyle
    })), children]
  });
});
JsonView.Bigint = Bigint;
JsonView.Date = Date;
JsonView.False = False;
JsonView.Float = Float;
JsonView.Int = Int;
JsonView.Map = Map;
JsonView.Nan = Nan;
JsonView.Null = Null;
JsonView.Set = Set;
JsonView.String = StringText;
JsonView.True = True;
JsonView.Undefined = Undefined;
JsonView.Url = Url;
JsonView.ValueQuote = ValueQuote;
JsonView.Arrow = Arrow;
JsonView.Colon = Colon;
JsonView.Quote = Quote;
JsonView.Ellipsis = Ellipsis;
JsonView.BraceLeft = BraceLeft;
JsonView.BraceRight = BraceRight;
JsonView.BracketsLeft = BracketsLeft;
JsonView.BracketsRight = BracketsRight;
JsonView.Copied = Copied;
JsonView.CountInfo = CountInfo;
JsonView.CountInfoExtra = CountInfoExtra;
JsonView.KeyName = KeyName;
JsonView.Row = Row;
JsonView.displayName = 'JVR.JsonView';

// Named exports for better import flexibility
export { JsonView };
// Named exports for type components
export { Bigint, Date as JsonDate, False, Float, Int, Map as JsonMap, Nan, Null, Set as JsonSet, StringText as JsonString, True, Undefined, Url };

// Named exports for symbol components
export { BraceLeft, BraceRight, BracketsLeft, BracketsRight, Arrow, Colon, Quote, ValueQuote };

// Named exports for section components
export { Copied, CountInfo, CountInfoExtra, Ellipsis, KeyName, Row };
export default JsonView;