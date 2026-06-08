import _extends from "@babel/runtime/helpers/extends";
import { useStore } from "../store.js";
import { useExpandsStore } from "../store/Expands.js";
import { BracketsClose } from "../symbol/index.js";
import { jsx as _jsx } from "react/jsx-runtime";
export var NestedClose = props => {
  var _expands$expandKey;
  var keyName = props.keyName,
    value = props.value,
    expandKey = props.expandKey,
    parentValue = props.parentValue,
    level = props.level,
    _props$keys = props.keys,
    keys = _props$keys === void 0 ? [] : _props$keys;
  var expands = useExpandsStore();
  var _useStore = useStore(),
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
  var len = Object.keys(value).length;
  if (isExpanded || len === 0) {
    return null;
  }
  var style = {
    paddingLeft: 4
  };
  var compProps = {
    keyName,
    value,
    keys,
    parentValue
  };
  var isArray = Array.isArray(value);
  var isMySet = value instanceof Set;
  return /*#__PURE__*/_jsx("div", {
    style: style,
    children: /*#__PURE__*/_jsx(BracketsClose, _extends({
      isBrackets: isArray || isMySet
    }, compProps, {
      isVisiable: true
    }))
  });
};
NestedClose.displayName = 'JVR.NestedClose';