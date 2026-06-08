import _extends from "@babel/runtime/helpers/extends";
import { Fragment, useRef } from 'react';
import { useStore } from "../store.js";
import { useExpandsStore } from "../store/Expands.js";
import { useShowToolsDispatch } from "../store/ShowTools.js";
import { Value } from "./Value.js";
import { KeyNameComp } from "../section/KeyName.js";
import { RowComp } from "../section/Row.js";
import { Container } from "../Container.js";
import { Quote, Colon } from "../symbol/index.js";
import { useHighlight } from "../utils/useHighlight.js";
import { Copied } from "../comps/Copied.js";
import { useIdCompat } from "../comps/useIdCompat.js";
import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
export var KeyValues = props => {
  var _expands$expandKey;
  var keyName = props.keyName,
    value = props.value,
    _props$expandKey = props.expandKey,
    expandKey = _props$expandKey === void 0 ? '' : _props$expandKey,
    level = props.level,
    _props$keys = props.keys,
    keys = _props$keys === void 0 ? [] : _props$keys,
    parentValue = props.parentValue;
  var expands = useExpandsStore();
  var _useStore = useStore(),
    objectSortKeys = _useStore.objectSortKeys,
    indentWidth = _useStore.indentWidth,
    collapsed = _useStore.collapsed,
    shouldExpandNodeInitially = _useStore.shouldExpandNodeInitially;
  var defaultExpanded = typeof collapsed === 'boolean' ? collapsed : typeof collapsed === 'number' ? level > collapsed : false;
  var isExpanded = (_expands$expandKey = expands[expandKey]) != null ? _expands$expandKey : shouldExpandNodeInitially ? false : defaultExpanded;
  var shouldExpand = shouldExpandNodeInitially && shouldExpandNodeInitially(!isExpanded, {
    value,
    keys,
    level,
    keyName,
    parentValue
  });
  if (shouldExpandNodeInitially && collapsed === false) {
    if (expands[expandKey] === undefined && !shouldExpand) {
      return null;
    }
  } else {
    if (expands[expandKey] === undefined && shouldExpand) {
      return null;
    }
  }
  if (isExpanded) {
    return null;
  }
  var isMyArray = Array.isArray(value);
  // object
  var entries = isMyArray ? Object.entries(value).map(m => [Number(m[0]), m[1]]) : Object.entries(value);
  if (objectSortKeys) {
    entries = objectSortKeys === true ? entries.sort((_ref, _ref2) => {
      var a = _ref[0];
      var b = _ref2[0];
      return typeof a === 'string' && typeof b === 'string' ? a.localeCompare(b) : 0;
    }) : entries.sort((_ref3, _ref4) => {
      var a = _ref3[0],
        valA = _ref3[1];
      var b = _ref4[0],
        valB = _ref4[1];
      return typeof a === 'string' && typeof b === 'string' ? objectSortKeys(a, b, valA, valB) : 0;
    });
  }
  var style = {
    borderLeft: 'var(--w-rjv-border-left-width, 1px) var(--w-rjv-line-style, solid) var(--w-rjv-line-color, #ebebeb)',
    paddingLeft: indentWidth,
    marginLeft: 6
  };
  return /*#__PURE__*/_jsx("div", {
    className: "w-rjv-wrap",
    style: style,
    children: entries.map((_ref5, idx) => {
      var key = _ref5[0],
        val = _ref5[1];
      return /*#__PURE__*/_jsx(KeyValuesItem, {
        parentValue: value,
        keyName: key,
        keys: [...keys, key],
        value: val,
        level: level
      }, idx);
    })
  });
};
KeyValues.displayName = 'JVR.KeyValues';
export var KayName = props => {
  var keyName = props.keyName,
    parentValue = props.parentValue,
    keys = props.keys,
    value = props.value;
  var _useStore2 = useStore(),
    highlightUpdates = _useStore2.highlightUpdates;
  var isNumber = typeof keyName === 'number';
  var highlightContainer = useRef(null);
  useHighlight({
    value,
    highlightUpdates,
    highlightContainer
  });
  var compProps = {
    keyName,
    value,
    keys,
    parentValue
  };
  return /*#__PURE__*/_jsxs(Fragment, {
    children: [/*#__PURE__*/_jsxs("span", {
      ref: highlightContainer,
      children: [/*#__PURE__*/_jsx(Quote, _extends({
        isNumber: isNumber,
        "data-placement": "left"
      }, compProps)), /*#__PURE__*/_jsx(KeyNameComp, _extends({}, compProps, {
        children: keyName
      })), /*#__PURE__*/_jsx(Quote, _extends({
        isNumber: isNumber,
        "data-placement": "right"
      }, compProps))]
    }), /*#__PURE__*/_jsx(Colon, _extends({}, compProps))]
  });
};
KayName.displayName = 'JVR.KayName';
export var KeyValuesItem = props => {
  var keyName = props.keyName,
    value = props.value,
    parentValue = props.parentValue,
    _props$level = props.level,
    level = _props$level === void 0 ? 0 : _props$level,
    _props$keys2 = props.keys,
    keys = _props$keys2 === void 0 ? [] : _props$keys2;
  var dispatch = useShowToolsDispatch();
  var subkeyid = useIdCompat();
  var isMyArray = Array.isArray(value);
  var isMySet = value instanceof Set;
  var isMyMap = value instanceof Map;
  var isDate = value instanceof Date;
  var isUrl = value instanceof URL;
  var isMyObject = value && typeof value === 'object' && !isMyArray && !isMySet && !isMyMap && !isDate && !isUrl;
  var isNested = isMyObject || isMyArray || isMySet || isMyMap;
  if (isNested) {
    var myValue = isMySet ? Array.from(value) : isMyMap ? Object.fromEntries(value) : value;
    return /*#__PURE__*/_jsx(Container, {
      keyName: keyName,
      value: myValue,
      parentValue: parentValue,
      initialValue: value,
      keys: keys,
      level: level + 1
    });
  }
  var reset = {
    onMouseEnter: () => dispatch({
      [subkeyid]: true
    }),
    onMouseLeave: () => dispatch({
      [subkeyid]: false
    })
  };
  return /*#__PURE__*/_jsxs(RowComp, _extends({
    className: "w-rjv-line",
    value: value,
    keyName: keyName,
    keys: keys,
    parentValue: parentValue
  }, reset, {
    children: [/*#__PURE__*/_jsx(KayName, {
      keyName: keyName,
      value: value,
      keys: keys,
      parentValue: parentValue
    }), /*#__PURE__*/_jsx(Value, {
      keyName: keyName,
      value: value,
      keys: keys
    }), /*#__PURE__*/_jsx(Copied, {
      keyName: keyName,
      value: value,
      keys: keys,
      parentValue: parentValue,
      expandKey: subkeyid
    })]
  }));
};
KeyValuesItem.displayName = 'JVR.KeyValuesItem';