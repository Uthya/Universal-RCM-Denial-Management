import { useTypesStore } from "../store/Types.js";
import { useTypesRender } from "../utils/useRender.js";
export var Date = props => {
  var _useTypesStore = useTypesStore(),
    _useTypesStore$Date = _useTypesStore.Date,
    Comp = _useTypesStore$Date === void 0 ? {} : _useTypesStore$Date;
  useTypesRender(Comp, props, 'Date');
  return null;
};
Date.displayName = 'JVR.Date';